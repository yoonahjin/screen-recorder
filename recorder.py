import os, sys, time, subprocess, threading, queue, shutil
from pathlib import Path
from datetime import datetime
import numpy as np
import soundcard as sc
import win32gui, win32con
from PySide6.QtCore import Qt, QTimer, QRect, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
 QLabel, QComboBox, QMessageBox, QListWidget, QDialog)

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
SAVE = Path(r'C:\Users\yun_a\Videos')
if not SAVE.parent.exists(): SAVE = Path.home() / 'Videos'
SAVE.mkdir(parents=True, exist_ok=True)

def ffmpeg():
    p = BASE / 'ffmpeg.exe'
    return str(p) if p.exists() else (shutil.which('ffmpeg') or '')

def windows():
    found=[]
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip() and not win32gui.IsIconic(hwnd):
            l,t,r,b=win32gui.GetWindowRect(hwnd)
            if r-l>100 and b-t>70: found.append((hwnd,win32gui.GetWindowText(hwnd)))
    win32gui.EnumWindows(cb,None)
    return found

class WindowPicker(QDialog):
    def __init__(self):
        super().__init__(); self.setWindowTitle('녹화할 창 선택'); self.resize(450,350)
        lay=QVBoxLayout(self); self.items=windows(); self.list=QListWidget()
        for _,title in self.items: self.list.addItem(title)
        lay.addWidget(self.list); btn=QPushButton('이 창 선택'); lay.addWidget(btn)
        btn.clicked.connect(self.accept); self.list.itemDoubleClicked.connect(self.accept)
    def rect(self):
        i=self.list.currentRow()
        if i<0: return None
        l,t,r,b=win32gui.GetWindowRect(self.items[i][0]); return l,t,r-l,b-t

class RegionPicker(QWidget):
    selected=Signal(tuple)
    def __init__(self):
        super().__init__(); self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen=QApplication.primaryScreen(); self.geo=screen.virtualGeometry(); self.setGeometry(self.geo)
        self.start=None; self.end=None; self.setCursor(Qt.CursorShape.CrossCursor)
    def paintEvent(self,e):
        p=QPainter(self); p.fillRect(self.rect(),QColor(0,0,0,90))
        if self.start and self.end:
            r=QRect(self.start,self.end).normalized(); p.setPen(QPen(QColor('#58c8ff'),3)); p.setBrush(QColor(88,200,255,40)); p.drawRect(r)
    def mousePressEvent(self,e): self.start=e.position().toPoint(); self.end=self.start; self.update()
    def mouseMoveEvent(self,e): self.end=e.position().toPoint(); self.update()
    def mouseReleaseEvent(self,e):
        self.end=e.position().toPoint(); r=QRect(self.start,self.end).normalized(); self.close()
        if r.width()>20 and r.height()>20:
            self.selected.emit((r.x()+self.geo.x(),r.y()+self.geo.y(),r.width(),r.height()))
    def keyPressEvent(self,e):
        if e.key()==Qt.Key.Key_Escape: self.close()

class Recorder(QObject):
    error=Signal(str)
    finished=Signal(str)
    def __init__(self):
        super().__init__(); self.proc=None; self.audio_thread=None; self.stop_event=threading.Event(); self.paused=threading.Event()
        self.started=0; self.paused_total=0; self.pause_at=0
    def start(self,rect):
        exe=ffmpeg()
        if not exe: raise RuntimeError('ffmpeg.exe를 프로그램 폴더에 넣어 주세요.')
        x,y,w,h=rect; w-=w%2; h-=h%2
        if w<32 or h<32: raise RuntimeError('녹화 영역이 너무 작습니다.')
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
        self.output=SAVE/f'녹화_{stamp}.mp4'; self.temp=SAVE/f'녹화_{stamp}.mkv'; self.wav=SAVE/f'녹화_{stamp}.wav'
        # FFmpeg gdigrab records visible desktop pixels; thread_queue_size helps reduce frame drops.
        encoders=subprocess.run([exe,'-hide_banner','-encoders'],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW).stdout
        encoder=['-c:v','h264_nvenc','-preset','p5','-cq','18','-b:v','0'] if 'h264_nvenc' in encoders else ['-c:v','libx264','-preset','veryfast','-crf','17']
        cmd=[exe,'-hide_banner','-loglevel','error','-y','-thread_queue_size','1024','-f','gdigrab','-framerate','30',
             '-offset_x',str(x),'-offset_y',str(y),'-video_size',f'{w}x{h}','-i','desktop',
             *encoder,'-pix_fmt','yuv420p','-f','matroska',str(self.temp)]
        self.stop_event.clear(); self.paused.clear()
        self.proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(.4)
        if self.proc.poll() is not None:
            msg=self.proc.stderr.read().decode(errors='replace'); raise RuntimeError('화면 녹화 시작 실패: '+msg[-700:])
        self.started=time.monotonic(); self.paused_total=0
        self.audio_thread=threading.Thread(target=self._audio,daemon=True); self.audio_thread.start()
    def _audio(self):
        import wave
        try:
            speaker=sc.default_speaker()
            if speaker is None: raise RuntimeError('기본 출력 장치를 찾을 수 없습니다.')
            loop=sc.get_microphone(id=str(speaker.id),include_loopback=True)
            with wave.open(str(self.wav),'wb') as wav:
                wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
                with loop.recorder(samplerate=48000,channels=2,blocksize=2048) as rec:
                    while not self.stop_event.is_set():
                        block=rec.record(numframes=2048)
                        if not self.paused.is_set():
                            wav.writeframes((np.clip(block,-1,1)*32767).astype('<i2').tobytes())
        except Exception as e: self.error.emit('시스템 오디오 녹음 오류: '+str(e))
    def pause(self):
        if not self.paused.is_set():
            self.paused.set(); self.pause_at=time.monotonic()
            # Windows process suspension is not used: pausing capture while keeping A/V sync is not supported by this backend.
    def resume(self):
        if self.paused.is_set(): self.paused_total+=time.monotonic()-self.pause_at; self.paused.clear()
    def elapsed(self):
        now=self.pause_at if self.paused.is_set() else time.monotonic()
        return max(0,int(now-self.started-self.paused_total))
    def stop(self):
        self.stop_event.set()
        if self.audio_thread: self.audio_thread.join(timeout=5)
        if self.proc and self.proc.poll() is None:
            try: self.proc.stdin.write(b'q\n'); self.proc.stdin.flush(); self.proc.wait(timeout=15)
            except Exception:
                self.proc.terminate(); self.proc.wait(timeout=5)
        exe=ffmpeg()
        # Mux with separate audio track; shortest prevents audio overrun.
        if self.temp.exists():
            cmd=[exe,'-hide_banner','-loglevel','error','-y','-i',str(self.temp)]
            if self.wav.exists() and self.wav.stat().st_size>44: cmd+=['-i',str(self.wav),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-shortest']
            else: cmd+=['-c:v','copy']
            cmd+=['-movflags','+faststart',str(self.output)]
            p=subprocess.run(cmd,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            if p.returncode: raise RuntimeError('MP4 저장 실패. 임시 MKV 파일은 보존되었습니다: '+p.stderr.decode(errors='replace')[-500:])
            self.temp.unlink(missing_ok=True); self.wav.unlink(missing_ok=True)
        self.finished.emit(str(self.output))

class Main(QWidget):
    def __init__(self):
        super().__init__(); self.setWindowTitle('간편 화면 녹화기'); self.resize(500,360); self.rec=Recorder(); self.region=None
        self.setStyleSheet('QWidget{background:#f6f8fc;color:#18263c;font-size:14px} QPushButton{background:#e3eaf4;border:0;border-radius:12px;padding:13px;font-weight:600} QPushButton:hover{background:#d4e1f2} QPushButton:disabled{color:#9aa5b6} QComboBox{background:white;border:1px solid #cbd5e1;border-radius:10px;padding:10px}')
        lay=QVBoxLayout(self); lay.setSpacing(16); lay.setContentsMargins(28,26,28,26)
        title=QLabel('● 간편 화면 녹화기'); title.setStyleSheet('font-size:23px;font-weight:800'); lay.addWidget(title)
        lay.addWidget(QLabel('녹화 방식'))
        self.mode=QComboBox(); self.mode.addItems(['전체 화면','창 선택','영역 지정']); lay.addWidget(self.mode)
        self.status=QLabel('● 대기 중'); self.status.setAlignment(Qt.AlignmentFlag.AlignCenter); self.status.setStyleSheet('font-size:17px;font-weight:700'); lay.addWidget(self.status)
        self.clock=QLabel('00:00:00'); self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter); self.clock.setStyleSheet('font-size:36px;font-weight:800'); lay.addWidget(self.clock)
        self.startbtn=QPushButton('● 녹화 시작'); self.startbtn.setStyleSheet('background:#e53945;color:white;border-radius:13px;padding:17px;font-size:18px;font-weight:800'); lay.addWidget(self.startbtn)
        row=QHBoxLayout(); self.pausebtn=QPushButton('일시정지'); self.stopbtn=QPushButton('■ 녹화 종료'); row.addWidget(self.pausebtn); row.addWidget(self.stopbtn); lay.addLayout(row)
        self.path=QLabel('저장 위치: '+str(SAVE)); self.path.setWordWrap(True); self.path.setStyleSheet('color:#64748b;font-size:12px'); lay.addWidget(self.path)
        self.startbtn.clicked.connect(self.choose); self.pausebtn.clicked.connect(self.pause); self.stopbtn.clicked.connect(self.stop)
        self.pausebtn.setEnabled(False); self.stopbtn.setEnabled(False)
        self.timer=QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(250)
        self.rec.error.connect(lambda s: QMessageBox.warning(self,'녹음 안내',s)); self.rec.finished.connect(self.done)
    def choose(self):
        mode=self.mode.currentIndex()
        if mode==0:
            g=QApplication.primaryScreen().geometry(); self.begin((g.x(),g.y(),g.width(),g.height()))
        elif mode==1:
            dlg=WindowPicker()
            if dlg.exec()==QDialog.DialogCode.Accepted:
                r=dlg.rect()
                if r:self.begin(r)
        else:
            self.hide(); QTimer.singleShot(300,self.pick)
    def pick(self):
        self.selector=RegionPicker(); self.selector.selected.connect(self.begin); self.selector.destroyed.connect(self.show); self.selector.show()
    def begin(self,rect):
        self.show()
        try:
            self.rec.start(rect); self.startbtn.setEnabled(False); self.mode.setEnabled(False); self.pausebtn.setEnabled(True); self.stopbtn.setEnabled(True); self.status.setText('🔴 녹화 중')
        except Exception as e: QMessageBox.critical(self,'녹화 시작 오류',str(e))
    def pause(self):
        if self.rec.paused.is_set(): self.rec.resume(); self.pausebtn.setText('일시정지'); self.status.setText('🔴 녹화 중')
        else: self.rec.pause(); self.pausebtn.setText('재개'); self.status.setText('Ⅱ 일시정지 (화면은 계속 기록됨)')
    def tick(self):
        if self.rec.proc and self.rec.proc.poll() is not None and self.stopbtn.isEnabled():
            self.status.setText('녹화 프로세스 종료됨'); self.stop()
        if self.rec.started:
            s=self.rec.elapsed(); self.clock.setText(f'{s//3600:02}:{s//60%60:02}:{s%60:02}')
    def stop(self):
        self.stopbtn.setEnabled(False); self.pausebtn.setEnabled(False); self.status.setText('저장 중...'); QApplication.processEvents()
        try:self.rec.stop()
        except Exception as e: QMessageBox.critical(self,'저장 오류',str(e)); self.reset()
    def done(self,path): QMessageBox.information(self,'저장 완료','MP4 저장 완료:\n'+path); self.reset()
    def reset(self):
        self.rec.proc=None; self.rec.started=0; self.startbtn.setEnabled(True); self.mode.setEnabled(True); self.pausebtn.setText('일시정지'); self.status.setText('● 대기 중'); self.clock.setText('00:00:00')
    def closeEvent(self,e):
        if self.stopbtn.isEnabled():
            QMessageBox.information(self,'녹화 중','녹화 종료 버튼을 눌러 저장한 후 닫아 주세요.'); e.ignore()
        else:e.accept()

if __name__=='__main__':
    import ctypes
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception: pass
    app=QApplication(sys.argv); app.setStyle('Fusion'); w=Main(); w.show(); sys.exit(app.exec())
