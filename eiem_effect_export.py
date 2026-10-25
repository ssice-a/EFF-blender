"""Publish a selected effect set, or merge it into an existing character Mod."""
from pathlib import Path
import importlib
import shutil
import subprocess
import tempfile


def publish(destination, effects, game_directory=''):
    from . import eiem_native_export as native
    from . import eiem_offline_reload as offline
    destination=Path(destination).resolve()
    modules,_=native.pack_modules()
    codec=importlib.import_module(modules['ini_reader'].__package__+'.runtime_controls')
    source_lock=modules['author_source'].source_read_lock
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Serialize against standalone exports and F9/F10 character snapshots. The
    # compiler below changes metadata only and never calls a native builder.
    with source_lock(destination.parent),offline.publication_lock(destination):
        ini=modules['ini_package'].ini_document(destination)
        if 'Mod' not in ini or ini['Mod'].get('type')=='effect':
            codec.publish_effect_mod(destination,destination.name,effects)
            return False
        if not (destination/'compiled.bin').is_file():
            raise ValueError('角色包缺少 compiled.bin，请先完整导出角色一次')
        cache={}
        def reader(path):return modules['ini_reader'].read_package(path,cache)
        previous=reader(destination)
        effects=[dict(row) for row in effects]
        for row in effects:
            parts={part for rule in previous['rules'] if any(
                t['sourcePath']==row['targetPath'] and t['sourceAsset']==row['targetAsset'] for t in rule['targets']) for part in rule['parts']}
            candidates=row.pop('_partCandidates',[])
            prior=next((e['part'] for e in previous['effects'] if e['id']==row['id'] and e['part'] in parts),'')
            row['part']=next((p for p in candidates if p in parts),prior)
        staging=Path(tempfile.mkdtemp(prefix='.'+destination.name+'-effects-',dir=destination.parent))
        try:
            staging.rmdir();native._clone_staging_tree(destination,staging)
            native._detach_staging_files([staging/'mod.ini'])
            codec.apply(staging,ini,effects=effects,merge_effects=True)
            candidate=reader(staging)
            # Both packages have passed real checksum validation. An unchanged
            # author does not need a protocol process or a compiled generation.
            if not any(native.package_changes(staging,destination,previous,candidate)):
                return True
            compiler,_,_,game=offline.reload_context(destination,game_directory,effect_update=True)
            # The compiler reads --update-effects' published input explicitly;
            # its output must be private, never an append to the cloned link.
            (staging/'compiled.bin').unlink(missing_ok=True)
            result=subprocess.run([str(compiler),'--update-effects',str(staging),str(destination),str(game)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or 'EFFECT-AUTHOR-UPDATED' not in result.stdout:
                raise ValueError('特效更新未发布，上一角色包保留：'+result.stdout[-1800:])
            # The compiler can add content-addressed blocks. Its completed
            # candidate, rather than the precompile author file set, publishes.
            candidate=reader(staging)
            native.publish(staging,destination,reader,previous=previous,candidate=candidate)
        finally:
            shutil.rmtree(staging,ignore_errors=True)
    return True
