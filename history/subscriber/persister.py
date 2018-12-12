import base64
import json
import time
import pymongo
from datetime import datetime
from dateutil.parser import parse
from history import conf
from dojot.module import Messenger, Config
from dojot.module.logger import Log

class Persister:

    def __init__(self):
        self.LOGGER = Log().color_log()
        self.db = None
        self.client = None

    def init_mongodb(self, collection_name=None):
        """
        MongoDB initialization

        :type collection_name: str
        :param collection_name: collection to create index 
        """
        try:
            self.client = pymongo.MongoClient(conf.db_host, replicaSet=conf.db_replica_set)
            self.db = self.client['device_history']
            if collection_name:
                self.create_indexes(collection_name)
            self.LOGGER.info("db initialized")
        except Exception as error:
            self.LOGGER.warn("Could not init mongo db client: %s" % error)

    def create_indexes(self, collection_name):
        """
        Create index given a collection

        :type collection_name: str
        :param collection_name: collection to create index 
        """
        self.db[collection_name].create_index([('ts', pymongo.DESCENDING)])
        self.db[collection_name].create_index('ts', expireAfterSeconds=conf.db_expiration)

    def enable_collection_sharding(self, collection_name):
        """
        Create index given a collection

        :type collection_name: str
        :param collection_name: collection to create index 
        """
        self.db[collection_name].create_index([('attr', pymongo.HASHED)])
        self.client.admin.command('enableSharding', self.db.name)
        self.client.admin.command('shardCollection', self.db[collection_name].full_name, key={'attr': 'hashed'})

    def parse_message(self, data):
        """
        Formats message to save in MongoDB

        :type data: dict
        :param data: data that will be parsed to a format
        """
        parsed_message = dict()
        parsed_message['attrs'] = data['data']['attrs']
        parsed_message['metadata'] = dict()
        parsed_message['metadata']['timestamp'] = int(time.time() * 1000)
        parsed_message['metadata']['deviceid'] = data['data']['id']
        parsed_message['metadata']['tenant'] = data['meta']['service']
        self.LOGGER.info("new message is: %s" % parsed_message)
        return json.dumps(parsed_message)

    def parse_datetime(self, timestamp):
        """
        Parses date time

        :type timestamp: string
        :param timestamp: timestamp
        """
        if timestamp is None:
            return datetime.utcnow()
        try:
            val = int(timestamp)
            if timestamp > ((2**31)-1):
                return datetime.utcfromtimestamp(val/1000)
            return datetime.utcfromtimestamp(float(timestamp))
        except ValueError as error:
            self.LOGGER.error("Failed to parse timestamp ({})\n{}".format(timestamp, error))
        try:
            return datetime.utcfromtimestamp(float(timestamp)/1000)
        except ValueError as error:
            self.LOGGER.error("Failed to parse timestamp ({})\n{}".format(timestamp, error))
        try:
            return parse(timestamp)
        except TypeError as error:
            raise TypeError('Timestamp could not be parsed: {}\n{}'.format(timestamp, error))

    def handle_event_data(self, tenant, message):
        """
            Given a device data event, persist it to mongo

            :type tenant: str
            :param tenant: tenant related to the event

            :type message: str
            :param message: A device data event
        """
        data = None
        try:
            data = json.loads(message)
            self.LOGGER.info("THIS IS THE DATA: %s" % data)
        except Exception as error:
            self.LOGGER.error('Received event is not valid JSON. Ignoring\n%s', error)
            return
        self.LOGGER.debug('got data event %s', message)
        metadata = data.get('metadata', None)
        if metadata is None:
            self.LOGGER.error('Received event has no metadata associated with it. Ignoring')
            return
        device_id = metadata.get('deviceid', None)
        if device_id is None:
            self.LOGGER.error('Received event cannot be traced to a valid device. Ignoring')
            return
        timestamp = self.parse_datetime(metadata.get('timestamp', None))
        docs = []
        for attr in data.get('attrs', {}).keys():
            docs.append({
                'attr': attr,
                'value': data['attrs'][attr],
                'device_id': device_id,
                'ts': timestamp
            })
        # Persist device status history as well
        device_status = metadata.get('status', None)
        if device_status is not None:
            docs.append({
                'status': device_status,
                'device_id': device_id,
                'ts': timestamp
            })
        if docs:
            try:
                collection_name = "{}_{}".format(tenant,device_id)
                self.db[collection_name].insert_many(docs)
            except Exception as error:
                self.LOGGER.warn('Failed to persist received information.\n%s', error)
        else:
            self.LOGGER.info('Got empty event from device [%s] - ignoring', device_id)

    def handle_event_devices(self, tenant, message):
        """
            Given a device management event, create (if not alredy existent) proper indexes
            to suppor the new device

            :type tenant: str
            :param tenant: tenant related to the event

            :type message: str
            :param message Device lifecyle message, as produced by device manager
        """
        data = json.loads(message)
        self.LOGGER.info('got device event %s', data)
        if(data['event'] != "configure"):
            collection_name = "{}_{}".format(data['meta']['service'], data['data']['id'])
            self.create_indexes(collection_name)
        else:
            new_message = self.parse_message(data)
            self.handle_event_data(tenant, new_message)


def main():
    """
    Main, inits mongo, messenger, create channels read channels for device
    and device-data topics and add callbacks to events related to that subjects
    """
    config = Config()
    persister = Persister()
    persister.init_mongodb()
    messenger = Messenger("Persister",config)
    messenger.init()
    messenger.create_channel(config.dojot['subjects']['devices'], "r")
    messenger.create_channel(config.dojot['subjects']['device_data'], "r")
    messenger.on(config.dojot['subjects']['devices'], "message", persister.handle_event_devices)
    messenger.on(config.dojot['subjects']['device_data'], "message", persister.handle_event_data)

if __name__=="__main__":
    main()