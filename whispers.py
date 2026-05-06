#!/usr/bin/env python3
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceStateChange
import socket
import threading
import json
import sys
import argparse
import time
import urllib.request
import urllib.error

# Попробуем импортировать miniupnpc (не критично, если отсутствует)
try:
    import miniupnpc
    HAS_UPNP = True
except ImportError:
    HAS_UPNP = False

# Глобальное состояние
my_id = None
my_port = None
my_external_ip = None          # внешний IP (если определён)
peers_config = {}              # peer_id -> (host, port)
connections = {}               # peer_id -> socket
incoming_requests = {}         # peer_id -> timestamp
active_chat = None
lock = threading.Lock()
print_lock = threading.Lock()

# Zeroconf
zeroconf = None
service_info = None
discovered_peers = set()

def safe_print(*args, **kwargs):
    """Потокобезопасный вывод."""
    with print_lock:
        print(*args, **kwargs)

def send_one(sock, msg_dict):
    """Отправить JSON-сообщение с переводом строки."""
    try:
        data = (json.dumps(msg_dict) + '\n').encode()
        sock.sendall(data)
    except Exception as e:
        safe_print(f"[ERROR] send failed: {e}")

def send_message(peer_id, msg_dict):
    """Отправить сообщение конкретному пиру по ID."""
    with lock:
        sock = connections.get(peer_id)
    if sock:
        send_one(sock, msg_dict)
    else:
        safe_print(f"[ERROR] No connection to {peer_id}")

def process_message(peer_id, msg):
    """Обработка входящего сообщения от peer_id."""
    global active_chat
    msg_type = msg.get('type')

    if msg_type == 'hello':
        # Используется только при рукопожатии, здесь игнорируем
        pass

    elif msg_type == 'chat_request':
        if msg.get('to') == my_id:
            with lock:
                incoming_requests[peer_id] = time.time()
            safe_print(f"\n[{peer_id}] wants to chat. Type /accept {peer_id} or /decline {peer_id}")

    elif msg_type == 'chat_accept':
        if msg.get('to') == my_id:
            safe_print(f"\n[{peer_id}] accepted chat request. Chat is now active.")
            with lock:
                active_chat = peer_id
            with lock:
                incoming_requests.pop(peer_id, None)

    elif msg_type == 'chat_decline':
        if msg.get('to') == my_id:
            safe_print(f"\n[{peer_id}] declined chat request.")
            with lock:
                incoming_requests.pop(peer_id, None)

    elif msg_type == 'message':
        if msg.get('to') == my_id:
            text = msg.get('text', '')
            safe_print(f"\r[{peer_id}] {text}\n> ", end='')

def peer_handler(conn, addr, is_outgoing=False, known_peer_id=None):
    """
    Универсальный обработчик соединения (входящего или исходящего).
    Выполняет рукопожатие hello, разрешает конфликт одновременных подключений,
    затем запускает цикл чтения сообщений.
    """
    peer_id = known_peer_id
    buffer = ""
    try:
        if is_outgoing:
            # Отправляем свой hello
            send_one(conn, {"type": "hello", "from": my_id})
            # Ждём ответный hello от сервера
            data = conn.recv(4096)
            if not data:
                conn.close()
                return
            buffer = data.decode()
            # Извлекаем первое JSON-сообщение (ожидаем hello)
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get('type') == 'hello':
                    peer_id = msg.get('from')
                    break
            if not peer_id or peer_id != known_peer_id:
                safe_print("[ERROR] Invalid hello response.")
                conn.close()
                return

            # Разрешение конфликта: если уже есть входящее соединение от того же пира,
            # следуем правилу: кто старше (чей ID больше), тот оставляет своё исходящее,
            # младший (меньший ID) должен переключиться на входящее.
            with lock:
                if peer_id in connections:
                    # Уже есть соединение – сравниваем ID
                    if my_id < peer_id:
                        # Мы младше, должны закрыть своё исходящее и использовать входящее.
                        # Закрываем текущее исходящее соединение.
                        conn.close()
                        return
                    else:
                        # Мы старше, оставляем своё исходящее, а входящее (которое уже в словаре)
                        # будет закрыто в потоке-обработчике того входящего соединения.
                        # В любом случае обновим словарь на наш сокет.
                        old_conn = connections[peer_id]
                        connections[peer_id] = conn
                        # Закрываем старое (входящее) соединение
                        old_conn.close()
                else:
                    # Обычный случай: соединений нет, сохраняем
                    connections[peer_id] = conn
        else:
            # Входящее соединение: ждём hello
            data = conn.recv(4096)
            if not data:
                conn.close()
                return
            buffer = data.decode()
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get('type') == 'hello':
                    peer_id = msg.get('from')
                    break
            if not peer_id:
                conn.close()
                return

            # Разрешение конфликта на стороне принимающего
            with lock:
                if peer_id in connections:
                    # Уже есть соединение – сравниваем ID
                    if my_id < peer_id:
                        # Мы младше – закрываем своё существующее (исходящее) и принимаем это входящее
                        old_conn = connections[peer_id]
                        connections[peer_id] = conn
                        old_conn.close()
                    else:
                        # Мы старше – оставляем своё существующее, входящее закрываем
                        conn.close()
                        return
                else:
                    connections[peer_id] = conn

            # Отправляем ответный hello
            send_one(conn, {"type": "hello", "from": my_id})
            safe_print(f"[INFO] Incoming connection from {peer_id} established.")

        # Основной цикл чтения сообщений
        while True:
            if buffer:
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    try:
                        msg = json.loads(line)
                    except:
                        continue
                    process_message(peer_id, msg)
            data = conn.recv(4096)
            if not data:
                break
            buffer += data.decode()

    except Exception as e:
        safe_print(f"[ERROR] peer_handler {peer_id}: {e}")
    finally:
        conn.close()
        with lock:
            if peer_id and peer_id in connections and connections[peer_id] is conn:
                del connections[peer_id]
            if peer_id and active_chat == peer_id:
                active_chat = None
        safe_print(f"[INFO] Connection with {peer_id or 'unknown'} closed.")

def connect_to_peer(peer_id, host, port):
    """Установить исходящее соединение и запустить обработчик."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        # Запускаем обработчик, который выполнит рукопожатие и сам зарегистрирует сокет
        threading.Thread(target=peer_handler, args=(sock, None, True, peer_id), daemon=True).start()
    except Exception as e:
        safe_print(f"[ERROR] Failed to connect to {peer_id} at {host}:{port}: {e}")

def server_thread():
    """Слушаем входящие подключения."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', my_port))
    server.listen(5)
    safe_print(f"[SERVER] Listening on port {my_port}")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=peer_handler, args=(conn, addr, False, None), daemon=True).start()

def get_local_ip():
    """Возвращает IP-адрес, используемый для локальной сети (в т.ч. VPN Hamachi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Пробуем адрес из диапазона Hamachi (10.0.0.0/8)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        # Запасной вариант – любой адрес
        s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def start_mdns():
    """Регистрируем сервис в mDNS (Zeroconf)."""
    global zeroconf, service_info
    local_ip = get_local_ip()
    service_name = f"{my_id}._whispers._tcp.local."
    service_info = ServiceInfo(
        "_whispers._tcp.local.",
        service_name,
        addresses=[socket.inet_aton(local_ip)],
        port=my_port,
        properties={'id': my_id}
    )
    zeroconf = Zeroconf()
    zeroconf.register_service(service_info)
    safe_print(f"[MDNS] Service registered as {service_name} at {local_ip}:{my_port}")

def stop_mdns():
    """Останавливаем mDNS."""
    global zeroconf, service_info
    if zeroconf and service_info:
        zeroconf.unregister_service(service_info)
        zeroconf.close()
        safe_print("[MDNS] Service unregistered.")

def on_service_state_change(zeroconf, service_type, name, state_change):
    """Обработчик обнаружения mDNS-сервисов."""
    if state_change == ServiceStateChange.Added:
        info = zeroconf.get_service_info(service_type, name)
        if info and info.properties:
            peer_id = info.properties.get(b'id', b'').decode()
            if peer_id and peer_id != my_id:
                addr = socket.inet_ntoa(info.addresses[0])
                port = info.port
                safe_print(f"[MDNS] Discovered {peer_id} at {addr}:{port}")
                with lock:
                    if peer_id not in connections and peer_id not in discovered_peers:
                        threading.Thread(target=connect_to_peer, args=(peer_id, addr, port), daemon=True).start()
                        discovered_peers.add(peer_id)
    elif state_change == ServiceStateChange.Removed:
        # При исчезновении сервиса удаляем из множества обнаруженных
        info = zeroconf.get_service_info(service_type, name)
        if info and info.properties:
            peer_id = info.properties.get(b'id', b'').decode()
        else:
            if name.endswith("._whispers._tcp.local."):
                peer_id = name.split("._")[0]
            else:
                peer_id = None
        if peer_id and peer_id in discovered_peers:
            discovered_peers.discard(peer_id)

# ---------- UPnP / внешний IP ----------
def setup_upnp(port):
    """Пробуем открыть порт через UPnP и получить внешний IP."""
    if not HAS_UPNP:
        return None
    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        if upnp.discover() > 0:
            upnp.selectigd()
            # Получаем внешний IP
            external_ip = upnp.externalipaddress()
            # Пробуем добавить маппинг порта
            upnp.addportmapping(port, 'TCP', upnp.lanaddr, port, 'Whispers Chat', '')
            safe_print(f"[UPNP] Port {port} mapped. External IP: {external_ip}")
            return external_ip
    except Exception as e:
        safe_print(f"[UPNP] Failed: {e}")
    return None

def get_external_ip_fallback():
    """Запасной метод определения внешнего IP через веб-сервис."""
    try:
        with urllib.request.urlopen('https://ifconfig.me/ip', timeout=5) as resp:
            ip = resp.read().decode().strip()
            return ip
    except Exception:
        pass
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=5) as resp:
            ip = resp.read().decode().strip()
            return ip
    except Exception:
        pass
    return None

def remove_upnp(port):
    """Удалить маппинг UPnP."""
    if not HAS_UPNP:
        return
    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        if upnp.discover() > 0:
            upnp.selectigd()
            upnp.deleteportmapping(port, 'TCP')
            safe_print("[UPNP] Port mapping removed.")
    except Exception as e:
        safe_print(f"[UPNP] Removal failed: {e}")

def cli():
    """Основной цикл обработки команд."""
    global active_chat
    time.sleep(0.5)
    safe_print("Welcome! Commands: /peers, /chat <id>, /accept <id>, /decline <id>, /quit")
    while True:
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue

        if cmd.startswith('/'):
            parts = cmd.split()
            if not parts:
                continue
            c = parts[0].lower()
            if c == '/quit':
                break
            elif c == '/peers':
                with lock:
                    if not connections:
                        safe_print("No connected peers.")
                    else:
                        safe_print("Connected peers:")
                        for p in connections:
                            mark = " (active chat)" if p == active_chat else ""
                            safe_print(f"  {p}{mark}")
            elif c == '/chat':
                if len(parts) < 2:
                    safe_print("Usage: /chat <peer_id>")
                else:
                    peer = parts[1]
                    if peer == my_id:
                        safe_print("Cannot chat with yourself.")
                        continue
                    with lock:
                        if peer not in connections:
                            safe_print(f"No connection to {peer}.")
                            continue
                    send_message(peer, {"type": "chat_request", "to": peer, "from": my_id})
                    safe_print(f"Chat request sent to {peer}.")
            elif c == '/accept':
                if len(parts) < 2:
                    safe_print("Usage: /accept <peer_id>")
                else:
                    peer = parts[1]
                    with lock:
                        if peer in incoming_requests:
                            del incoming_requests[peer]
                        else:
                            safe_print(f"No pending request from {peer}.")
                            continue
                    send_message(peer, {"type": "chat_accept", "to": peer, "from": my_id})
                    with lock:
                        active_chat = peer
                    safe_print(f"Chat with {peer} is now active.")
            elif c == '/decline':
                if len(parts) < 2:
                    safe_print("Usage: /decline <peer_id>")
                else:
                    peer = parts[1]
                    with lock:
                        if peer in incoming_requests:
                            del incoming_requests[peer]
                        else:
                            safe_print(f"No pending request from {peer}.")
                            continue
                    send_message(peer, {"type": "chat_decline", "to": peer, "from": my_id})
                    safe_print(f"Declined chat with {peer}.")
            else:
                safe_print("Unknown command.")
        else:
            if active_chat:
                send_message(active_chat, {"type": "message", "to": active_chat, "from": my_id, "text": cmd})
                safe_print(f"[You] {cmd}")
            else:
                safe_print("No active chat. Use /chat <id> to start one.")

def main():
    global my_id, my_port, my_external_ip, peers_config

    parser = argparse.ArgumentParser(description="Decentralized console chat peer.")
    parser.add_argument('--id', required=True, help='Your unique ID')
    parser.add_argument('--port', type=int, required=True, help='Listening port')
    parser.add_argument('--peers', default='', help='Comma-separated peer spec: id:host:port')
    parser.add_argument('--no-upnp', action='store_true', help='Disable UPnP port mapping')
    parser.add_argument('--external-ip', help='Manually specify external IP address')
    args = parser.parse_args()

    my_id = args.id
    my_port = args.port

    # Разбор пиров из аргументов
    if args.peers:
        for spec in args.peers.split(','):
            spec = spec.strip()
            if not spec:
                continue
            try:
                pid, host, port_str = spec.split(':')
                port = int(port_str)
                peers_config[pid] = (host, port)
            except:
                safe_print(f"Invalid peer spec: {spec}")
                sys.exit(1)

    # Внешний IP: явный > UPnP > fallback
    if args.external_ip:
        my_external_ip = args.external_ip
        safe_print(f"[INFO] Using specified external IP: {my_external_ip}")
    else:
        if not args.no_upnp:
            my_external_ip = setup_upnp(my_port)
        if not my_external_ip:
            safe_print("[INFO] Trying fallback IP detection...")
            my_external_ip = get_external_ip_fallback()
            if my_external_ip:
                safe_print(f"[INFO] Detected external IP (fallback): {my_external_ip}")
                safe_print("[WARNING] Port forwarding may still be required!")
    if my_external_ip:
        safe_print(f"[INFO] This node is potentially reachable at: {my_external_ip}:{my_port}")
    else:
        safe_print("[WARNING] Could not determine external IP. You may be unreachable from outside the local network.")

    # Запуск mDNS и серверного сокета
    start_mdns()
    browser = ServiceBrowser(zeroconf, "_whispers._tcp.local.", handlers=[on_service_state_change])
    threading.Thread(target=server_thread, daemon=True).start()

    # Подключаемся к заданным вручную пирам
    for pid, (host, port) in peers_config.items():
        if pid == my_id:
            continue
        threading.Thread(target=connect_to_peer, args=(pid, host, port), daemon=True).start()

    try:
        cli()
    finally:
        # Очистка
        stop_mdns()
        with lock:
            for sock in list(connections.values()):
                try:
                    sock.close()
                except:
                    pass
            connections.clear()
        if my_external_ip and not args.external_ip and not args.no_upnp:
            remove_upnp(my_port)
        safe_print("Shutting down...")

if __name__ == '__main__':
    main()
