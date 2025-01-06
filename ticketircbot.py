import configparser
import sqlite3
import irc.bot
import irc.strings
import random
import string
import threading
import time
import re

class HelpBot(irc.bot.SingleServerIRCBot):
    def __init__(self, config):
        self.config = config
        self.db = self.setup_database()
        self.user_last_msg_time = {}
        self.msg_cool_down = 10
        server = config['DEFAULT']['server']
        port = int(config['DEFAULT']['port'])
        channel = config['DEFAULT']['channel']
        opers_channel = config['DEFAULT']['opers_channel']
        nickname = config['DEFAULT']['nickname']
        

        irc.bot.SingleServerIRCBot.__init__(self, [(server, port)], nickname, nickname)
        self.channel = channel
        self.opers_channel = opers_channel
        self.channel_members = set()
        self.db = self.setup_database()
        
    
    def on_ctcp(self, connection, event):
        """Handle CTCP requests such as VERSION and PING."""
        if event.arguments[0] == "VERSION":
            connection.ctcp_reply(event.source.nick, "VERSION: HelpBot with ticket support v.1.0")
        elif event.arguments[0] == "PING":
            if len(event.arguments) > 1:
                connection.ctcp_reply(event.source.nick, f"PONG {event.arguments[1]}")
    
    def sanitize_input(self, input_string):
        """Sanitize input to remove unwanted characters."""
        safe_string = re.sub(r'[^a-zA-Z0-9 .,!?;:@]', '', input_string)
        return safe_string

    def generate_ticket_id(self, prefix="ID", length=8):
        characters = string.ascii_uppercase + string.digits
        random_string = ''.join(random.choice(characters) for i in range(length))
        return prefix + random_string

    def setup_database(self):
        connection = sqlite3.connect('helpbot.db')
        cursor = connection.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                nickname TEXT,
                message TEXT,
                status TEXT DEFAULT 'Waiting',
                oper TEXT
            )
        ''')
        connection.commit()
        return connection

    def create_ticket(self, nickname, message):
        ticket_id = self.generate_ticket_id()
        cursor = self.db.cursor()
        cursor.execute('INSERT INTO tickets (id, nickname, message, status) VALUES (?, ?, ?, "Waiting")', (ticket_id, nickname, message))
        self.db.commit()
        return ticket_id

    def update_ticket_status(self, ticket_id, status, oper=None):
        cursor = self.db.cursor()
        cursor.execute('UPDATE tickets SET status = ?, oper = ? WHERE id = ?', (status, oper, ticket_id))
        self.db.commit()

    def on_welcome(self, connection, event):
        self.connection = connection  # Ensure the connection is stored
        connection.join(self.channel)
        connection.join(self.opers_channel)
        self.schedule_write()  # Start writing to HTML after the connection is established

    def on_join(self, connection, event):
        nickname = irc.strings.lower(event.source.nick)
        self.channel_members.add(nickname)
        if nickname != irc.strings.lower(self.connection.get_nickname()):
            message = f"Hola, {nickname}. Por favor envíame un mensaje privado con tu consulta."
            connection.privmsg(nickname, message)

    def on_part(self, connection, event):
        nickname = irc.strings.lower(event.source.nick)
        self.channel_members.discard(nickname)

    def on_quit(self, connection, event):
        nickname = irc.strings.lower(event.source.nick)
        self.channel_members.discard(nickname)

    def on_privmsg(self, connection, event):
        nickname = irc.strings.lower(event.source.nick)
        current_time = time.time()
        if nickname in self.channel_members:
            if nickname in self.user_last_msg_time and (current_time - self.user_last_msg_time[nickname]) < self.msg_cool_down:
                connection.privmsg(nickname, f"Please wait for {self.msg_cool_down} seconds between messages.")
                return

            self.user_last_msg_time[nickname] = current_time
            message = event.arguments[0]
            sanitized_message = self.sanitize_input(message)  # Make sure this is correctly called
            ticket_id = self.create_ticket(nickname, sanitized_message)
            connection.privmsg(nickname, f"Su solicitud de ayuda ha sido recibida. Su número de ticket es {ticket_id}. Puedes comprobar el estado de tu ticket en cualquier momento con !status {ticket_id} en el canal #ayuda.")
            connection.privmsg(self.opers_channel, f"Nuevo ticket creado: #{ticket_id} por {nickname}. Problemas: {sanitized_message}")
        else:
            connection.privmsg(nickname, "Debes ser miembro del canal #ayuda para solicitar ayuda.")


    def on_pubmsg(self, connection, event):
        if irc.strings.lower(event.target) == irc.strings.lower(self.channel):
            message = event.arguments[0]
            if message.startswith("!status"):
                parts = message.split()
                if len(parts) == 2:
                    ticket_id = parts[1]
                    cursor = self.db.cursor()
                    cursor.execute('SELECT status FROM tickets WHERE id = ?', (ticket_id,))
                    row = cursor.fetchone()
                    if row:
                        ticket_status = row[0]
                        connection.privmsg(self.channel, f"Estado del ticket {ticket_id}: {ticket_status}.")
                    else:
                        connection.privmsg(self.channel, "No se encontró ningún ticket con esa ID.")
        elif irc.strings.lower(event.target) == irc.strings.lower(self.opers_channel):
            self.handle_operator_commands(connection, event)

    def handle_operator_commands(self, connection, event):
        message = event.arguments[0]
        if message.startswith("!close") or message.startswith("!open"):
            parts = message.split()
            if len(parts) == 3:
                command, ticket_id, oper_nickname = parts
                new_status = 'CLOSED' if command == "!close" else 'OPEN'
                cursor = self.db.cursor()
                cursor.execute('SELECT nickname FROM tickets WHERE id = ?', (ticket_id,))
                row = cursor.fetchone()
                if row:
                    self.update_ticket_status(ticket_id, new_status, oper_nickname if new_status == 'CLOSED' else None)
                    connection.privmsg(self.opers_channel, f"El estado del ticket: #{ticket_id} ha sido actualizado a {new_status} por {oper_nickname}.")
                    if new_status == 'OPEN':
                        connection.privmsg(row[0], f"{oper_nickname} te ayudará en breve con el ticket: #{ticket_id}.")
                        connection.mode(self.channel, "+v " + row[0])
                else:
                    connection.privmsg(self.opers_channel, "No se encontró ningún ticket con ese ID.")

    def write_tickets_to_html(self):
        """Create a new database connection for thread safety and fetch data to write to HTML."""
        # Establish a new connection inside this method
        connection = sqlite3.connect('helpbot.db')
        cursor = connection.cursor()
        cursor.execute("SELECT id, status, oper FROM tickets")
        rows = cursor.fetchall()

        html_content = "<html><head><title>Estado de tickets del canal #ayuda</title></head><body>"
        html_content += "<table border='1'><tr><th>Ticket ID</th><th>Status</th><th>Operator</th></tr>"

        for row in rows:
            html_content += f"<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2] or 'N/A'}</td></tr>"

        html_content += "</table></body></html>"

        with open("/var/www/html/tickets_status.html", "w") as file:
            file.write(html_content)

        # Notify the channel that tickets are being updated
        self.connection.privmsg(self.channel, "Actualizando tickets...")
        connection.close()

    def schedule_write(self):
        """Schedule the write_tickets_to_html function to run every 5 minutes."""
        if hasattr(self, 'connection'):  # Check if the connection exists
            self.write_tickets_to_html()
            threading.Timer(3600, self.schedule_write).start()
        else:
            threading.Timer(5, self.schedule_write).start()  # Retry after 5 seconds if not connected

if __name__ == "__main__":
    config = configparser.ConfigParser()
    config.read('config.ini')
    bot = HelpBot(config)
    bot.start()
