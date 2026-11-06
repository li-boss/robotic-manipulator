"""只用伪ROS传输，不连接设备或真实ROS节点。"""
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from ros_client import RosClient


class ProtocolTests(unittest.TestCase):
    def transport(self,bad_hash=False):
        sent=[]; holder={}; destroyed=[]
        def publish(message):
            req=json.loads(message.data);sent.append(req)
            digest=hashlib.sha256(json.dumps(req['input'],sort_keys=True).encode()).hexdigest()
            holder['reply']=SimpleNamespace(data=json.dumps(dict(id=req['id'],input_hash='wrong' if bad_hash else digest,
                plan=dict(hardware=False))))
        node=SimpleNamespace(create_subscription=lambda kind,topic,callback,depth:holder.update(callback=callback),
            create_publisher=lambda *a:SimpleNamespace(get_subscription_count=lambda:1,publish=publish),
            destroy_node=lambda:destroyed.append(True))
        def spin(*a,**kw):
            if 'reply' in holder:holder['callback'](holder.pop('reply'))
        ros=SimpleNamespace(context=SimpleNamespace(Context=lambda:object()),init=lambda **kw:None,
            create_node=lambda *a,**kw:node,spin_once=spin,shutdown=lambda **kw:None)
        executor=SimpleNamespace(SingleThreadedExecutor=lambda **kw:SimpleNamespace(
            add_node=lambda node:None,spin_once=spin,shutdown=lambda:None))
        return {'rclpy':ros,'rclpy.executors':executor,'std_msgs':SimpleNamespace(),
            'std_msgs.msg':SimpleNamespace(String=SimpleNamespace)},sent,destroyed

    def test_health_publishes_once_and_destroys_node(self):
        modules,sent,destroyed=self.transport()
        with patch.dict('sys.modules',modules):self.assertEqual(RosClient().health(),dict(hardware=False))
        self.assertEqual(len(sent),1);self.assertEqual(sent[0]['input'],dict(operation='health'))
        self.assertEqual(destroyed,[True])

    def test_version_mismatch_rejects_without_republish(self):
        modules,sent,destroyed=self.transport(True)
        with patch.dict('sys.modules',modules):
            with self.assertRaises(ValueError):RosClient().health()
        self.assertEqual(len(sent),1);self.assertEqual(destroyed,[True])


if __name__=='__main__':unittest.main()
