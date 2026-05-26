import socket
for port in [5100, 5101, 5102]:
    s = socket.socket()
    try:
        s.connect(("localhost", port))
        print(f"Port {port}: IN_USE")
    except:
        print(f"Port {port}: FREE")
    finally:
        s.close()