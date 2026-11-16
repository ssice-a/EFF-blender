import copy
import unittest
import eiem_effect_document as document
import eiem_effect_scope as scope


class EffectScopeTests(unittest.TestCase):
    def fixture(self):
        def node(i, parent, children):
            return dict(identity='cab:'+str(i), transform='cab:'+str(i+1),
                        parent='cab:'+str(parent+1) if parent else None,
                        children=['cab:'+str(c+1) for c in children],
                        components=['cab:'+str(i+1), 'cab:'+str(i+2), 'cab:'+str(i+3)])
        nodes=[node(10,0,[20,30]),node(20,10,[40]),node(30,10,[]),node(40,20,[])]
        objects=[];values={}
        for n in nodes:
            for i,identity in enumerate([n['identity'],*n['components']]):
                objects.append(dict(identity=identity,classId=[1,4,198,199][i],
                    native=dict(cab='cab',pathId=int(identity.split(':')[1])),references=[]))
                values[identity]={}
        return document.Editor(dict(format='EFF_EFFECT_WORKSPACE',version=1,
            catalog=dict(root='cab:10',hierarchy=nodes,objects=objects),values=values))

    def test_child_selection_keeps_descendants_and_only_transform_ancestors(self):
        edit=self.fixture();before=copy.deepcopy(edit.state)
        selected=scope.resolve(edit,['cab:20'])
        self.assertEqual(set(selected),{'cab:20','cab:40'})
        ids=scope.scene_ids(edit,selected)
        self.assertTrue({'cab:10','cab:11','cab:22','cab:42'}.issubset(ids))
        self.assertFalse({'cab:12','cab:13','cab:30','cab:32'} & ids)
        self.assertEqual(edit.state,before)

    def test_subemitter_dependency_and_roundtrip_scope(self):
        edit=self.fixture()
        edit.value('cab:22')['SubModule']={'system':dict(m_FileID=0,m_PathID=32)}
        edit.objects['cab:22']['references']=[dict(field='$.SubModule.system',fileId=0,pathId=32,identity='cab:32')]
        selected=scope.resolve(edit,['cab:20'])
        self.assertEqual(set(selected),{'cab:20','cab:30','cab:40'})
        scope.restore(edit,list(selected))
        self.assertEqual(edit.state['emitter_scope'],list(selected))
        root=next(n for n in edit.state['catalog']['hierarchy'] if n['identity']=='cab:10')
        self.assertEqual(root['components'],['cab:11'])

    def test_missing_emitter_rejected(self):
        with self.assertRaisesRegex(ValueError,'导出的发射器'):
            scope.resolve(self.fixture(),['cab:404'])

    def test_payload_removes_excluded_additions_and_keeps_placement_scaffold(self):
        edit=self.fixture()
        nodes=edit.state['catalog']['hierarchy']
        # Treat both siblings and the selected child's parent as author clones.
        for node in nodes[1:]:
            for identity in [node['identity'],*node['components']]:
                edit.objects[identity]['template']='cab:'+str(10+(int(identity.split(':')[1])%10))
        def ptr(identity):return dict(m_FileID=0,m_PathID=int(identity.split(':')[1]))
        additions=[]
        for node in nodes[1:]:
            for identity in [node['identity'],*node['components']]:
                kind=edit.objects[identity]['classId']
                data=dict(m_Component=[dict(component=ptr(i)) for i in node['components']]) if kind==1 else dict(m_GameObject=ptr(node['identity']))
                if kind==4:data.update(m_Father=ptr(node['parent']),m_Children=[ptr(i) for i in node['children']])
                edit.state['values'][identity]=data
                additions.append(dict(identity=identity,template=edit.objects[identity]['template'],data=data))
        value=dict(objects=[],emitters=dict(added=additions,removed=[]))
        result=scope.payload(edit,value,['cab:40'])
        added={r['identity']:r['data'] for r in result['emitters']['added']}
        self.assertEqual(set(added),{'cab:20','cab:21','cab:40','cab:41','cab:42','cab:43'})
        self.assertEqual(added['cab:20']['m_Component'],[dict(component=ptr('cab:21'))])
        self.assertEqual(len(value['emitters']['added']),12,'selection mutated author declarations')


if __name__=='__main__':unittest.main()
