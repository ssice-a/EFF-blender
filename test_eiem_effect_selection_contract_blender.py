"""Selection, rather than mount ownership, is the export contract."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import bpy

directory = Path(__file__).resolve().parent
sys.path.insert(0, str(directory.parent))
spec = importlib.util.spec_from_file_location('EFFSelectionContract', directory / '__init__.py',
    submodule_search_locations=[str(directory)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package.register()
fx = package.eiem_blender_addon.effect_editor

def obj(name, mesh=False):
    value = bpy.data.objects.new(name, bpy.data.meshes.new(name) if mesh else None)
    bpy.context.collection.objects.link(value)
    return value

owner = obj('Selected sword', True)
owner.data['eiem_section'] = 'MeshSword'
root = obj('Attached effect')
root['eff_effect_document'] = '{}'
root['eff_effect_id'] = 'EffectTest'
root.eff_effect.owner = owner
root.eff_effect.enabled = True
root.eff_effect.usage = fx.MESH_ATTACHMENT
emitter = obj('Selected emitter'); emitter.parent = root
emitter['eff_effect_node'] = 'emitter'
resource = obj('Resource mesh', True); resource.parent = root
document = SimpleNamespace(state={'catalog': {'hierarchy': [
    {'identity': 'emitter', 'components': ['particle']}]}}, objects={'particle': {'classId': 198}})
addon = SimpleNamespace(mesh_export_template=lambda mesh: mesh)
try:
    with patch.object(fx, 'editor', return_value=document):
        assert fx.export_roots([], [owner], addon) == [], 'mount ownership silently exported effects'
        assert fx.export_selections([owner], [owner], addon) == []
        assert fx.export_selections([resource]) == [], 'resource Mesh selected its parent effect'
        selected = fx.export_selections([owner, emitter], [owner], addon)
        assert len(selected) == 1 and selected[0].emitters == ('emitter',), 'Mesh broadened selected emitter scope'
        assert fx.export_selections([owner, root], [owner], addon) == [fx.EffectSelection(root)]
        assert fx.export_selections([emitter, root]) == [fx.EffectSelection(root)]
        assert fx.export_selections([root, emitter]) == [fx.EffectSelection(root)]
        api=package.eiem_blender_addon
        context=SimpleNamespace(selected_objects=[owner])
        assert api.selected_export_resources(context)[:3]==([owner],[],[])
        context.selected_objects=[owner,emitter]
        assert api.selected_export_resources(context)[2]==[fx.EffectSelection(root,('emitter',))]
finally:
    package.unregister()
print('PASS explicit Mesh/root/emitter export selection contract')
