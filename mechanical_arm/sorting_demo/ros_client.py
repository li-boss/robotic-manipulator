"""只向纯计算ROS2节点请求计划；不导入硬件库。"""
import hashlib
import json
import threading
import time
import uuid


class RosClient:
    def __init__(self):
        self.lock=threading.Lock()

    def __call__(self,setup,arms,xyz,require_path=True):
        return self.request(dict(setup=setup,arms=arms,xyz_mm=xyz,require_path=require_path))

    def health(self):
        return self.request(dict(operation='health'))

    def request(self,data):
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from std_msgs.msg import String
        except ImportError as exc:
            raise ValueError('ROS2规划环境未就绪；请通过项目启动器加载Humble环境') from exc
        with self.lock:
            context=rclpy.context.Context();rclpy.init(context=context)
            node=rclpy.create_node('jjt_web_plan_'+uuid.uuid4().hex[:8],context=context)
            executor=SingleThreadedExecutor(context=context);executor.add_node(node)
            result=[];identifier=uuid.uuid4().hex
            def response(msg):
                try:
                    value=json.loads(msg.data)
                    if value.get('id')==identifier:result.append(value)
                except ValueError:pass
            subscription=node.create_subscription(String,'/jjt/auto_plan/response',response,10)
            publisher=node.create_publisher(String,'/jjt/auto_plan/request',10)
            digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
            try:
                deadline=time.monotonic()+3
                while publisher.get_subscription_count()==0 and time.monotonic()<deadline:
                    executor.spin_once(timeout_sec=.1)
                if publisher.get_subscription_count()==0:raise ValueError('ROS2规划服务未连接，未发送运动')
                message=String();message.data=json.dumps(dict(id=identifier,input=data))
                publisher.publish(message)  # 一次请求；不重发。
                deadline=time.monotonic()+30
                while not result and time.monotonic()<deadline:executor.spin_once(timeout_sec=.1)
                if not result:raise ValueError('ROS2规划超时；未发送运动')
                value=result[0]
                if value.get('input_hash')!=digest:raise ValueError('ROS2响应输入版本不匹配')
                if value.get('error'):raise ValueError(value['error'])
                return value['plan']
            finally:executor.shutdown();node.destroy_node();rclpy.shutdown(context=context)
