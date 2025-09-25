import socket

with open("C:\\Users\\ramil\\AppData\\Roaming\\MetaQuotes\\Terminal\\Common\\Files\\http_debug.txt", "rb") as f:
    raw = f.read()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(("127.0.0.1", 8000))
s.sendall(raw)
response = s.recv(4096)
print(response.decode(errors="ignore"))
s.close()