import sqlite3
import json
import time
from pathlib import Path

class SqliteService:
    """SQLite database service for storing MQTT messages and measurement data"""
    
    def __init__(self, db_path: str = "mqtt_data.db"):
        """
        Initialize SQLite service
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_conn = None
        self.connect()
        self.create_tables()
    
    def connect(self):
        """Establish database connection"""
        try:
            self.db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.db_conn.row_factory = sqlite3.Row  # Access columns by name
            print(f"Connected to SQLite database: {self.db_path}")
        except sqlite3.Error as e:
            print(f"ERROR: Failed to connect to database: {e}")
            raise
    
    def create_tables(self):
        """Create database tables if they don't exist"""
        if not self.db_conn:
            return
        
        cursor = self.db_conn.cursor()
        try:
            # Create table for MQTT messages
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mqtt_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    topic TEXT,
                    payload TEXT,
                    from_id TEXT,
                    to_id TEXT,
                    command TEXT
                )
            ''')
            
            # Create table for measurement data
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS measurement_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    from_id TEXT,
                    data TEXT
                )
            ''')
            
            # Create table for device logs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    device_id TEXT,
                    event_type TEXT,
                    details TEXT
                )
            ''')
            
            self.db_conn.commit()
            print("Database tables created/verified")
        except sqlite3.Error as e:
            print(f"ERROR: Failed to create tables: {e}")
            raise
    
    # =============================
    # MQTT Message Operations
    # =============================
    
    def store_mqtt_message(self, topic: str, payload: dict, from_id: str, to_id: str, command: str):
        """
        Store MQTT message in database
        
        Args:
            topic: MQTT topic
            payload: Message payload (dict)
            from_id: Sender device ID
            to_id: Recipient device ID
            command: Command type
        """
        if not self.db_conn:
            return False
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO mqtt_messages (timestamp, topic, payload, from_id, to_id, command)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (time.time(), topic, json.dumps(payload), from_id, to_id, command))
            self.db_conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"ERROR: Failed to store MQTT message: {e}")
            return False
    
    def get_recent_messages(self, limit: int = 10, from_id: str = None):
        """
        Retrieve recent MQTT messages
        
        Args:
            limit: Number of messages to retrieve
            from_id: Optional filter by sender ID
        
        Returns:
            List of message records
        """
        if not self.db_conn:
            return []
        
        try:
            cursor = self.db_conn.cursor()
            if from_id:
                cursor.execute('''
                    SELECT * FROM mqtt_messages 
                    WHERE from_id = ? 
                    ORDER BY timestamp DESC LIMIT ?
                ''', (from_id, limit))
            else:
                cursor.execute('''
                    SELECT * FROM mqtt_messages 
                    ORDER BY timestamp DESC LIMIT ?
                ''', (limit,))
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"ERROR: Failed to retrieve messages: {e}")
            return []
    
    def get_messages_by_command(self, command: str, limit: int = 10):
        """
        Retrieve messages by command type
        
        Args:
            command: Command to filter by
            limit: Number of messages to retrieve
        
        Returns:
            List of message records
        """
        if not self.db_conn:
            return []
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                SELECT * FROM mqtt_messages 
                WHERE command = ? 
                ORDER BY timestamp DESC LIMIT ?
            ''', (command, limit))
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"ERROR: Failed to retrieve messages by command: {e}")
            return []
    
    # =============================
    # Measurement Data Operations
    # =============================
    
    def store_measurement_data(self, from_id: str, data: dict):
        """
        Store measurement data
        
        Args:
            from_id: Device ID sending measurement
            data: Measurement data (dict)
        """
        if not self.db_conn:
            return False
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO measurement_data (timestamp, from_id, data)
                VALUES (?, ?, ?)
            ''', (time.time(), from_id, json.dumps(data)))
            self.db_conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"ERROR: Failed to store measurement data: {e}")
            return False
    
    def get_recent_measurements(self, limit: int = 10, from_id: str = None):
        """
        Retrieve recent measurement data
        
        Args:
            limit: Number of records to retrieve
            from_id: Optional filter by device ID
        
        Returns:
            List of measurement records
        """
        if not self.db_conn:
            return []
        
        try:
            cursor = self.db_conn.cursor()
            if from_id:
                cursor.execute('''
                    SELECT * FROM measurement_data 
                    WHERE from_id = ? 
                    ORDER BY timestamp DESC LIMIT ?
                ''', (from_id, limit))
            else:
                cursor.execute('''
                    SELECT * FROM measurement_data 
                    ORDER BY timestamp DESC LIMIT ?
                ''', (limit,))
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"ERROR: Failed to retrieve measurements: {e}")
            return []
    
    def get_measurements_by_time_range(self, from_time: float, to_time: float, device_id: str = None):
        """
        Retrieve measurements within a time range
        
        Args:
            from_time: Start timestamp
            to_time: End timestamp
            device_id: Optional filter by device ID
        
        Returns:
            List of measurement records
        """
        if not self.db_conn:
            return []
        
        try:
            cursor = self.db_conn.cursor()
            if device_id:
                cursor.execute('''
                    SELECT * FROM measurement_data 
                    WHERE timestamp BETWEEN ? AND ? AND from_id = ?
                    ORDER BY timestamp DESC
                ''', (from_time, to_time, device_id))
            else:
                cursor.execute('''
                    SELECT * FROM measurement_data 
                    WHERE timestamp BETWEEN ? AND ?
                    ORDER BY timestamp DESC
                ''', (from_time, to_time))
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"ERROR: Failed to retrieve measurements by time range: {e}")
            return []
    
    # =============================
    # Device Log Operations
    # =============================
    
    def log_device_event(self, device_id: str, event_type: str, details: str = ""):
        """
        Log a device event
        
        Args:
            device_id: Device ID
            event_type: Type of event (e.g., "CONNECT", "DISCONNECT", "ERROR")
            details: Event details
        """
        if not self.db_conn:
            return False
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO device_logs (timestamp, device_id, event_type, details)
                VALUES (?, ?, ?, ?)
            ''', (time.time(), device_id, event_type, details))
            self.db_conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"ERROR: Failed to log device event: {e}")
            return False
    
    def get_device_logs(self, device_id: str, limit: int = 20):
        """
        Retrieve logs for a device
        
        Args:
            device_id: Device ID
            limit: Number of logs to retrieve
        
        Returns:
            List of log records
        """
        if not self.db_conn:
            return []
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                SELECT * FROM device_logs 
                WHERE device_id = ? 
                ORDER BY timestamp DESC LIMIT ?
            ''', (device_id, limit))
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"ERROR: Failed to retrieve device logs: {e}")
            return []
    
    # =============================
    # Database Maintenance
    # =============================
    
    def clear_old_data(self, days: int = 30):
        """
        Delete data older than specified number of days
        
        Args:
            days: Number of days to keep
        
        Returns:
            Number of records deleted
        """
        if not self.db_conn:
            return 0
        
        try:
            cursor = self.db_conn.cursor()
            cutoff_time = time.time() - (days * 86400)  # seconds in a day
            
            # Delete old messages
            cursor.execute('DELETE FROM mqtt_messages WHERE timestamp < ?', (cutoff_time,))
            messages_deleted = cursor.rowcount
            
            # Delete old measurements
            cursor.execute('DELETE FROM measurement_data WHERE timestamp < ?', (cutoff_time,))
            measurements_deleted = cursor.rowcount
            
            # Delete old logs
            cursor.execute('DELETE FROM device_logs WHERE timestamp < ?', (cutoff_time,))
            logs_deleted = cursor.rowcount
            
            self.db_conn.commit()
            total_deleted = messages_deleted + measurements_deleted + logs_deleted
            print(f"Deleted {total_deleted} old records (older than {days} days)")
            return total_deleted
        except sqlite3.Error as e:
            print(f"ERROR: Failed to clear old data: {e}")
            return 0
    
    def get_database_stats(self):
        """
        Get database statistics
        
        Returns:
            Dictionary with record counts
        """
        if not self.db_conn:
            return {}
        
        try:
            cursor = self.db_conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM mqtt_messages')
            messages_count = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM measurement_data')
            measurements_count = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM device_logs')
            logs_count = cursor.fetchone()['count']
            
            return {
                'mqtt_messages': messages_count,
                'measurement_data': measurements_count,
                'device_logs': logs_count
            }
        except sqlite3.Error as e:
            print(f"ERROR: Failed to get database stats: {e}")
            return {}
    
    def close(self):
        """Close database connection"""
        if self.db_conn:
            self.db_conn.close()
            print("Database connection closed")


# Global instance for easy access
_db_service = None

def get_sqlite_service(db_path: str = "mqtt_data.db"):
    """
    Get or create global SQLite service instance
    
    Args:
        db_path: Path to SQLite database file
    
    Returns:
        SqliteService instance
    """
    global _db_service
    if _db_service is None:
        _db_service = SqliteService(db_path)
    return _db_service


if __name__ == "__main__":
    # Example usage
    db = SqliteService("test_mqtt.db")
    
    # Store sample MQTT message
    db.store_mqtt_message(
        topic="mqtt/from/iot",
        payload={"distance": 10.5, "lines": 5},
        from_id="iot_001",
        to_id="android_001",
        command="#MEASURE_DATA"
    )
    
    # Store sample measurement
    db.store_measurement_data(
        from_id="iot_001",
        data={"wheel_distance": 15.2, "timestamp": time.time()}
    )
    
    # Log device event
    db.log_device_event(
        device_id="iot_001",
        event_type="CONNECT",
        details="Device connected successfully"
    )
    
    # Retrieve and display data
    print("\n=== Recent Messages ===")
    messages = db.get_recent_messages(limit=5)
    for msg in messages:
        print(f"From: {msg['from_id']}, Command: {msg['command']}, Time: {msg['timestamp']}")
    
    print("\n=== Database Stats ===")
    stats = db.get_database_stats()
    print(f"Total Messages: {stats.get('mqtt_messages', 0)}")
    print(f"Total Measurements: {stats.get('measurement_data', 0)}")
    print(f"Total Logs: {stats.get('device_logs', 0)}")
    
    db.close()
