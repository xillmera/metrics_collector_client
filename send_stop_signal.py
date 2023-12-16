import socket

sock = socket.socket()
sock.connect(('localhost', 8080))
sock.send(b'stop')

data = sock.recv(1024)
sock.close()

print (data)