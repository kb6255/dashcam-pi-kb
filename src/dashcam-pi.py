

import io
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.encoders import MJPEGEncoder
from picamera2.encoders import Quality
from picamera2.outputs import FileOutput
from picamera2.outputs import FfmpegOutput
import socketserver
import threading
from threading import Condition
from http import server
import os
import datetime
import time
from PIL import Image

# global variables
camera_enabled = True
streaming = False
recording = False
global recording_thread
folder = '../DCIM/'

# make new directories if they don't exist
cmd = 'mkdir -p ' + folder + 'PHOTO ' + folder + 'VIDEO ' + folder + 'THUMB'
fp = os.popen(cmd)
fp.close()

# get my IP address
cmd = 'ip addr show wlan0 | grep global | grep inet | awk \'{print $2}\''
fp = os.popen(cmd)
IPAddr = fp.read() 
fp.close()
IPAddr = IPAddr[:IPAddr.index('/')]

# get index.html template content
page = ''
with open('index.html', 'r') as f:
  page = f.read()

# web video streaming content
class StreamingOutput(io.BufferedIOBase):
  def __init__(self):  #仓库
    self.frame = None            # 存储最新的一帧JPEG数据
    self.condition = Condition() # 线程同步锁，用于生产者/消费者模型

  def write(self, buf): #生产者
    if buf.startswith(b'\xff\xd8'):    # JPEG帧的开始标志，表示新的一帧数据开始了
      with self.condition:          
        self.frame = buf               # 更新为最新帧
        self.condition.notify_all()    # 通知所有等待的线程有新帧可用
'''
# 导入 HTTP 服务器基础类，处理网页请求
class StreamingHandler(server.BaseHTTPRequestHandler):

    # 处理所有 GET 请求（浏览器访问网页、点击按钮都会触发）
    def do_GET(self):
        # 声明要使用的全局变量（跨函数共享状态）
        global page
        global camera_enabled
        global streaming
        global recording
        global recording_thread
        global folder

        # ======================
        # 1. 访问根路径 / → 跳转到首页
        # ======================
        if self.path == '/':
            self.send_response(301)                  # 301 = 永久重定向
            self.send_header('Location', '/index.html') # 跳转到主页
            self.end_headers()

        # ======================
        # 2. 访问首页 index.html（核心页面）
        # ======================
        elif self.path == '/index.html':
            content = page  # page 是提前加载好的网页模板

            # 根据摄像头是否可用，控制网页元素显示/隐藏
            if camera_enabled:
                # 摄像头正常：显示画面，隐藏错误提示
                content = content.replace('\'<!--##VISIBILITY##-->\'', 'visible') \
                          .replace('\'<!--##ERROR##-->\'', 'hidden')
            else:
                # 摄像头异常：隐藏画面，显示错误
                content = content.replace('\'<!--##VISIBILITY##-->\'', 'hidden') \
                          .replace('\'<!--##ERROR##-->\'', 'visible')

            # 根据【实时预览】状态，切换按钮文字和链接
            if streaming:
                # 正在预览 → 显示“停止预览”按钮
                content = content.replace('<!--##HREF_PREVIEW##-->', '/stop_streaming') \
                          .replace('<!--##BUTTON_PREVIEW##-->', 'STOP PREVIEW') \
                          .replace('<!--##IMG_STREAM##-->', '<img src="stream.mjpg" alt="camera preview">')
            else:
                # 未预览 → 显示“开始预览”按钮
                content = content.replace('<!--##HREF_PREVIEW##-->', '/start_streaming') \
                          .replace('<!--##BUTTON_PREVIEW##-->', 'START PREVIEW')

            # 根据【录像】状态，切换按钮文字
            if recording:
                # 正在录像 → 显示“停止录像”
                content = content.replace('<!--##HREF_RECORD##-->', '/stop_recording') \
                          .replace('<!--##BUTTON_RECORD##-->', 'STOP VIDEO')
            else:
                # 未录像 → 显示“开始录像”
                content = content.replace('<!--##HREF_RECORD##-->', '/start_recording') \
                          .replace('<!--##BUTTON_RECORD##-->', 'START VIDEO')

            # 把网页发给浏览器
            content = content.encode('utf-8')
            self.send_response(200)                          # 200 = 请求成功
            self.send_header('Content-Type', 'text/html')     # 告诉浏览器这是HTML
            self.send_header('Content-Length', len(content)) # 内容长度
            self.end_headers()
            self.wfile.write(content)  # 输出网页内容

        # ======================
        # 3. 开始实时预览
        # ======================
        elif self.path == ('/start_streaming'):
            if not streaming:  # 如果没在推流，才启动
                # 启动摄像头低分辨率流，推到 StreamingOutput
                camera.start_recording(encoder = streaming_encoder, output = FileOutput(output), name = 'lores')
                time.sleep(0.2)
                streaming = True  # 标记状态：正在预览

            # 操作完跳回首页
            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

        # ======================
        # 4. 开始录像
        # ======================
        elif self.path == '/start_recording':      
            if not recording:
                recording = True  # 标记状态：正在录像
                # 开新线程录像（不卡住网页）
                recording_thread = threading.Thread(target = record_video)
                recording_thread.start()

            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

        # ======================
        # 5. 停止预览
        # ======================
        elif self.path == ('/stop_streaming'):
            if streaming:
                camera.stop_encoder(streaming_encoder)  # 停止推流编码器
                streaming = False

            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

        # ======================
        # 6. 停止录像
        # ======================
        elif self.path == '/stop_recording':
            if recording:
                recording = False  # 标记停止
                recording_thread.join()  # 等待录像线程安全结束

            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

        # ======================
        # 7. 拍照功能
        # ======================
        elif self.path == '/take_photo':
            # 用时间戳生成唯一文件名
            x = datetime.datetime.now()
            filename = folder + 'PHOTO/dcp_' + x.strftime('%Y%m%d%H%M%S') + '.jpg'

            # 保存照片 + 生成缩略图
            save_with_thumbnail(filename)

            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

        # ======================
        # 8. 重启树莓派
        # ======================
        elif self.path == '/restart':
            stop_video()  # 先安全停止摄像头
            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

            # 执行系统重启命令
            cmd = 'sudo shutdown -r now'
            fp = os.popen(cmd)
            fp.close()      

        # ======================
        # 9. 关机树莓派
        # ======================
        elif self.path == '/turnoff':
            stop_video()
            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()

            cmd = 'sudo shutdown now'
            fp = os.popen(cmd)
            fp.close()

        # ======================
        # 10. 视频流地址：stream.mjpg（核心！网页画面来源）
        # ======================
        elif self.path == '/stream.mjpg' and streaming:
            # 返回 MJPG 实时视频流
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')  # 不缓存
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()

            try:
                # 无限循环：不断给浏览器发最新帧
                while True:
                    with output.condition:
                        output.condition.wait()  # 等待生产者（摄像头）产生新图
                        frame = output.frame     # 拿到最新一帧

                    # 把帧发给浏览器（MJPG 格式要求）
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                # 浏览器断开连接会报错，直接忽略
                pass

        # ======================
        # 11. 加载网页图标 favicon.png
        # ======================
        elif self.path == '/favicon.png':
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.end_headers()
            with open(self.path.replace('/', '', 1), 'rb') as f:
                self.wfile.write(f.read())

        # ======================
        # 12. 加载样式表 styles.css
        # ======================
        elif self.path == '/styles.css':
            self.send_response(200)
            self.send_header('Content-Type', 'text/css')
            self.end_headers()
            with open(self.path.replace('/', '', 1), 'rb') as f:
                self.wfile.write(f.read())

        # ======================
        # 13. 相册页面 gallery.html
        # ======================
        elif self.path == '/gallery.html':
            with open('gallery.html', 'r') as f:
                content = f.read()
            # 自动插入所有照片/视频列表到网页
            content = content.replace('<!--##IMG_GALLERY##-->', get_files())
            content = content.encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)

        # ======================
        # 14. 浏览器访问照片 .jpg
        # ======================
        elif folder.replace('..', '') in self.path and '.jpg' in self.path:
            self.path = self.path.replace('/', '../', 1)
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.end_headers()
            try:
                with open(self.path, 'rb') as f:
                    self.wfile.write(f.read())
            except Exception as e:
                pass

        # ======================
        # 15. 浏览器访问视频 .mp4
        # ======================
        elif folder.replace('..', '') in self.path and '.mp4' in self.path:
            self.path = self.path.replace('/', '../', 1)
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.end_headers()
            try:
                with open(self.path, 'rb') as f:
                    self.wfile.write(f.read())
            except Exception as e:
                pass

        # ======================
        # 16. 其他地址 → 统一跳回首页
        # ======================
        else:
            self.send_response(302)
            self.send_header('Location', '/index.html')
            self.end_headers()
'''
# HTTP request handler
class StreamingHandler(server.BaseHTTPRequestHandler):
  def do_GET(self):
    global page
    global camera_enabled
    global streaming
    global recording
    global recording_thread
    global folder
    # base path redirects to index.html
    if self.path == '/':
      self.send_response(301)
      self.send_header('Location', '/index.html')
      self.end_headers()     
    elif self.path == '/index.html':
      # substitute custom html tags with paths and text in user interface template (index.html content)
      content = page
      if camera_enabled:
        content = content.replace('\'<!--##VISIBILITY##-->\'', 'visible') \
                  .replace('\'<!--##ERROR##-->\'', 'hidden')
      else:
        content = content.replace('\'<!--##VISIBILITY##-->\'', 'hidden') \
                  .replace('\'<!--##ERROR##-->\'', 'visible')
      if streaming:
        content = content.replace('<!--##HREF_PREVIEW##-->', '/stop_streaming') \
                  .replace('<!--##BUTTON_PREVIEW##-->', 'STOP PREVIEW') \
                  .replace('<!--##IMG_STREAM##-->', '<img src="stream.mjpg" alt="camera preview">')
      else:
        content = content.replace('<!--##HREF_PREVIEW##-->', '/start_streaming') \
                  .replace('<!--##BUTTON_PREVIEW##-->', 'START PREVIEW')
      if recording:
        content = content.replace('<!--##HREF_RECORD##-->', '/stop_recording') \
                  .replace('<!--##BUTTON_RECORD##-->', 'STOP VIDEO')
      else:
        content = content.replace('<!--##HREF_RECORD##-->', '/start_recording') \
                  .replace('<!--##BUTTON_RECORD##-->', 'START VIDEO')
      content = content.encode('utf-8')
      self.send_response(200)
      self.send_header('Content-Type', 'text/html')
      self.send_header('Content-Length', len(content))
      self.end_headers()
      self.wfile.write(content)
    elif self.path == ('/start_streaming'):
      if not streaming:
        # start video stream (live preview) using streaming encoder and lower resolution camera image stream
        camera.start_recording(encoder = streaming_encoder, output = FileOutput(output), name = 'lores')
        time.sleep(0.2)
        streaming = True
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()    
    elif self.path == '/start_recording':      
      if not recording:
        # start video recording in separate thread
        recording = True
        recording_thread = threading.Thread(target = record_video)
        recording_thread.start()
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
    elif self.path == ('/stop_streaming'):
      if streaming:
        # stop video stream (live preview)
        camera.stop_encoder(streaming_encoder)
        streaming = False
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
    elif self.path == '/stop_recording':
      if recording:
        # stop video recording
        recording = False
        recording_thread.join()
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
    elif self.path == '/take_photo':
      # define picture filename from timestamp
      x = datetime.datetime.now()
      filename = folder + 'PHOTO/dcp_' + x.strftime('%Y%m%d%H%M%S') + '.jpg'
      # save picture from main camera image stream and make its thumbnail copy
      save_with_thumbnail(filename)
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
    elif self.path == '/restart':
      # restart Raspberry Pi
      stop_video()
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
      cmd = 'sudo shutdown -r now'
      fp = os.popen(cmd)
      fp.close()      
    elif self.path == '/turnoff':
      # shut down Raspberry Pi
      stop_video()
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()
      cmd = 'sudo shutdown now'
      fp = os.popen(cmd)
      fp.close()
    elif self.path == '/stream.mjpg' and streaming:
      # get and return video stream content
      self.send_response(200)
      self.send_header('Age', 0)
      self.send_header('Cache-Control', 'no-cache, private')
      self.send_header('Pragma', 'no-cache')
      self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
      self.end_headers()
      # read and return video stream output buffer
      try:
        while True:
          with output.condition:
            output.condition.wait()
            frame = output.frame
          self.wfile.write(b'--FRAME\r\n')
          self.send_header('Content-Type', 'image/jpeg')
          self.send_header('Content-Length', len(frame))
          self.end_headers()
          self.wfile.write(frame)
          self.wfile.write(b'\r\n')
      except Exception as e:
        pass
    elif self.path == '/favicon.png':
      # get and return favicon.png
      self.send_response(200)
      self.send_header('Content-Type', 'image/png')
      self.end_headers()
      with open(self.path.replace('/', '', 1), 'rb') as f:
        self.wfile.write(f.read())
    elif self.path == '/styles.css':
      # get and return styles.css
      self.send_response(200)
      self.send_header('Content-Type', 'text/css')
      self.end_headers()
      with open(self.path.replace('/', '', 1), 'rb') as f:
        self.wfile.write(f.read())
    elif self.path == '/gallery.html':
      # get gallery.html content and substitute custom html tag with list of paths to videos and pictures
      with open('gallery.html', 'r') as f:
        content = f.read()
      content = content.replace('<!--##IMG_GALLERY##-->', get_files())
      content = content.encode('utf-8')
      self.send_response(200)
      self.send_header('Content-Type', 'text/html')
      self.send_header('Content-Length', len(content))
      self.end_headers()
      self.wfile.write(content)
    elif folder.replace('..', '') in self.path and '.jpg' in self.path:
      # get and return .jpg file from corrected path
      self.path = self.path.replace('/', '../', 1)
      self.send_response(200)
      self.send_header('Content-Type', 'image/jpeg')
      self.end_headers()
      try:
        with open(self.path, 'rb') as f:
          self.wfile.write(f.read())
      except Exception as e:
        pass
    elif folder.replace('..', '') in self.path and '.mp4' in self.path:
      # get and return .mp4 file from corrected path
      self.path = self.path.replace('/', '../', 1)
      self.send_response(200)
      self.send_header('Content-Type', 'video/mp4')
      self.end_headers()
      try:
        with open(self.path, 'rb') as f:
          self.wfile.write(f.read())
      except Exception as e:
        pass
    else:
      # for unknown path, redirect to index.html, it's better than return code 404
      self.send_response(302)
      self.send_header('Location', '/index.html')
      self.end_headers()

# streaming server class
class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
  allow_reuse_address = True
  daemon_threads = True

# capture picture from main camera image stream and make its thumbnail copy
def save_with_thumbnail(origin):
  thumb = origin.replace('PHOTO', 'THUMB')
  camera.capture_file(origin)
  img = Image.open(origin)
  img.thumbnail((160, 90), Image.NEAREST)
  img.save(thumb)

# capture video using recording encoder and main high resolution camera stream
def record_video():
  global recording
  global folder
  x = None
  d = 0
  # recording flag that stops this loop is set from the main thread
  while recording:
    y = datetime.datetime.now()
    if x is not None:
      d = (y - x).total_seconds()
    if x is None or d > 60:
      # every video file contains 60 seconds of recording
      x = y
      filename = folder + 'VIDEO/dcp_' + x.strftime('%Y%m%d%H%M%S') + '.mp4'
      try:
        camera.stop_encoder(recording_encoder)
      except:
        pass
      camera.start_recording(encoder = recording_encoder, output = FfmpegOutput(output_filename = filename, audio = False), quality = quality, name = 'main')
      # every video file gets its thumbnail picture for gallery
      save_with_thumbnail(filename.replace('VIDEO/dcp_', 'THUMB/dcpvid_').replace('.mp4', '.jpg'))
    time.sleep(2)
  camera.stop_encoder(recording_encoder)

# stop video recording
def stop_video():
  global camera_enabled
  if camera_enabled:
    camera.stop_recording()
    camera.stop()

# get list of videos and pictures from DCIM subfolders and return as gallery content
def get_files():
  files = ''
  # list all dcp_*.jpg and dcp_*mp4 files in DCIM folder and its subfolders excluding THUMB subfolder, extract timestamp, path name, then sort from newest to oldest file, return path with filename
  cmd = 'find .. -type f \( -iname "dcp_*.jpg" -o -iname "dcp_*.mp4" -path "*/DCIM/*" \) \( -not -path "*/THUMB/*" \) -printf "%T@\t%p\n" | sort -n -r | cut -f2-'
  fp = os.popen(cmd)
  for line in fp:
    if '.mp4' in line:
      files = files + '<video wihth="160" height="90" controls preload="none" poster="%s"><source src="%s" type="video/mp4"></video>\n' %  \
        (line.replace('VIDEO', 'THUMB').replace('dcp_', 'dcpvid_').replace('.mp4', '.jpg'), line)
    if '.jpg' in line:
      files = files + '<img wihth="160" height="90" src="%s" alt="photo">\n' % line.replace('PHOTO', 'THUMB')
  fp.close()
  return files

# Raspberry Pi camera module settings for main high resolution stream and lower resolution stream
# main stream is for video and pictures, lores is for web streaming
# change resolutions and quality for your purposes
try:
  camera = Picamera2()
  output = StreamingOutput()
  main_stream = {'size': (1920, 1080)}
  lores_stream = {'size': (640, 360), 'format': 'YUV420'}
  config = camera.create_video_configuration(main_stream, lores_stream, encode = 'lores')
  camera.configure(config)
  camera.start()
  streaming_encoder = MJPEGEncoder()
  streaming_encoder.quality = 20
  recording_encoder = H264Encoder(10000000)
  quality = Quality.VERY_HIGH

  # uncomment these lines if you want to start video recording right away
  #recording = True
  #recording_thread = threading.Thread(target = record_video)
  #recording_thread.start()
except Exception as e:
  camera_enabled = False
  print(e)

# start streaming server
try:
  address = ('', 8000)
  print('IP address http://%s:%s' % (IPAddr, address[1]))
  server = StreamingServer(address, StreamingHandler)
  server.serve_forever()
finally:
  stop_video()