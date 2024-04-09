#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scan for Bluetooth Low Energy packets and attempt to identify them.
"""

import os
import sys
import asyncio
import argparse
import logging
from dotenv import load_dotenv
from queue import Queue

from src.sniffer import Sniffer
from src.grpc_routes import GrpcRoutes

messageQueue: Queue = Queue()

REQUIRE_PLATFORM = "linux"

class Main:
    args = None

    grpcRoutes: GrpcRoutes
    sniffer: Sniffer
    address: str
    x_coordinate: float
    y_coordinate: float

    def __init__(self):
        parser = argparse.ArgumentParser(
            prog="btlesniffer",
            description="Scan for Bluetooth Low Energy devices and gather "
                        "information about them. This program will only run on "
                        "Linux systems."
        )
        parser.add_argument(
            "-v", "--verbose",
            action="count",
            default=0,
            help="increase the verbosity of the program"
        )
        parser.add_argument(
            "-d", "--debug",
            action="store_true",
            help="enable debugging features"
        )
        parser.add_argument(
            "--threshold-rssi",
            type=int,
            default=-80,
            help="the lower bound received signal strength (RSSI) at which to "
                "attempt to connect to devices (in dBa, default -80 dBa)."
        )
        parser.add_argument(
            "--connection-polling-interval",
            type=int,
            default=5,
            help="how frequently the sniffer shall go through the device registry "
                "and attempt to establish connections (in seconds, default 5 s)."
        )
        self.args = parser.parse_args()

        if sys.platform != REQUIRE_PLATFORM:
            raise RuntimeError("You must run this programme on Linux.")

        # Setup Logging
        if self.args.verbose == 1:
            log_level = logging.INFO
        elif self.args.verbose >= 2 or self.args.debug:
            log_level = logging.DEBUG
        else:
            log_level = logging.WARNING

        logging.basicConfig(level=log_level)

        # Setup GRPC server
        self.address = os.getenv("GRPC_SERVER_ADDRESS")
        self.x_coordinate = float(os.getenv("LOCATION_X"))
        self.y_coordinate = float(os.getenv("LOCATION_Y"))



    def run(self):
        try:
            self.grpcRoutes = GrpcRoutes(messageQueue, self.address, self.x_coordinate, self.y_coordinate)
            with Sniffer(logging.getLogger(), 
                        self.args.threshold_rssi,
                        self.args.connection_polling_interval) as sniffer:
                sniffer.run()
                asyncio.get_event_loop().run_until_complete(self.grpcRoutes.run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    load_dotenv()
    main = Main()
    main.run()
