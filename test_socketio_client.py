import socketio

sio = socketio.Client()

@sio.on('collector_stats')
def on_stats(data):
    print(f"收到 collector_stats: {data}")

@sio.on('connect')
def on_connect():
    print("Socket.IO 已连接")
    sio.emit('subscribe', {'session_id': 'sess_064d91e954b8'})

@sio.on('disconnect')
def on_disconnect():
    print("Socket.IO 断开")

# Connect
sio.connect('http://localhost:5100')
import time
time.sleep(6)
sio.disconnect()
print("测试完成")