"""개인 PC 실행: 서버 준비 후 브라우저 열기."""
import threading
import time
import webbrowser
import uvicorn

def open_browser():
    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/',timeout=.5) as r:
                if r.status==200:
                    webbrowser.open('http://127.0.0.1:8000/')
                    return
        except OSError:
            time.sleep(.25)

if __name__=='__main__':
    threading.Thread(target=open_browser,daemon=True).start()
    uvicorn.run('app.main:app',host='127.0.0.1',port=8000)
