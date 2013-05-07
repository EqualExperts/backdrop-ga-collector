import base64
from datetime import timedelta, datetime
import json
import logging

from requests.exceptions import HTTPError
from dateutil import parser
from gapy.client import from_private_key, from_secrets_file
from gapy.error import GapyError
import requests

from backdrop import load_json, get_credentials
from backdrop.collector.datetimeutil import to_datetime, period_range, to_utc
from backdrop.collector.jsonencoder import JSONEncoder


logging.basicConfig(level=logging.DEBUG)


def _create_client(credentials):
    if "CLIENT_SECRETS" in credentials:
        return from_secrets_file(
            credentials['CLIENT_SECRETS'],
            storage_path=credentials['STORAGE_PATH']

        )
    else:
        return from_private_key(
            credentials['ACCOUNT_NAME'],
            private_key_path=credentials['PRIVATE_KEY'],
            storage_path=credentials['STORAGE_PATH']
        )


def query_ga(client, config, start_date, end_date):
    return client.query.get(
        config["id"].replace("ga:", ""),
        start_date,
        end_date,
        config["metrics"],
        config["dimensions"]
    )


def send_data(data, config):
    url = config["url"]
    data = json.dumps(data, cls=JSONEncoder)
    headers = {
        "Content-type": "application/json",
        "Authorization": "Bearer " + config["token"]
    }

    logging.debug("Posting:\n%s" % data)

    response = requests.post(url, data=data, headers=headers)

    logging.info("Received response:\n%s" % response.text)

    response.raise_for_status()


def data_id(data_type, timestamp, period, dimension_values):
    return base64.urlsafe_b64encode("_".join(
        [data_type, to_utc(timestamp).strftime("%Y%m%d%H%M%S"), period] + dimension_values
    ))


def build_document(item, data_type, start_date, end_date):
    if data_type is None:
        raise ValueError("Must provide a data type")
    period = "week"
    base_properties = {
        "_id": data_id(
            data_type, to_datetime(start_date), period,
            item.get("dimensions", {}).values()
        ),
        "_start_at": to_datetime(start_date),
        "_end_at": to_datetime(end_date + timedelta(days=1)),
        "_period": period,
        "dataType": data_type
    }
    dimensions = item.get("dimensions", {}).items()
    metrics = [(key, int(value)) for key, value in item["metrics"].items()]
    return dict(base_properties.items() + dimensions + metrics)


def run(config_path, start_date=None, end_date=None):
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")
    try:
        config = load_json(config_path)

        start_date = parser.parse(start_date)
        end_date = parser.parse(end_date)

        credentials = get_credentials()

        client = _create_client(credentials)

        documents = []

        for start, end in period_range(start_date, end_date):
            response = query_ga(client, config["query"], start, end)

            documents += [build_document(item, config["dataType"], start, end)
                          for item in response]

        if any(documents):
            send_data(documents, config["target"])

    except HTTPError:
        logging.exception("Unable to send data to target")
        exit(-3)

    except GapyError:
        logging.exception("Unable to retrieve data from Google Analytics")
        exit(-2)

    except Exception as e:
        logging.exception(e)
        exit(-1)
