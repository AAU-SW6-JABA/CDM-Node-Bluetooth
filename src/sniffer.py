# -*- coding: utf-8 -*-

"""
Provide a class that uses the D-Bus API of BlueZ to capture nearby Bluetooth
devices.
"""

import logging
import pydbus
from gi.repository import GLib
from queue import Queue
import time

from .Message import Message

from .util import SERVICE_NAME, DEVICE_INTERFACE, OBJECT_MANAGER_INTERFACE, \
    PROPERTIES_INTERFACE, find_adapter, GATT_SERVICE_INTERFACE, \
    GATT_CHARACTERISTIC_INTERFACE, GATT_DESCRIPTOR_INTERFACE, get_known_devices
from .device import GATTService, GATTCharacteristic, GATTDescriptor, Device, \
    print_device


class Sniffer(object):
    """
    Capture reachable Bluetooth devices and attempt to fingerprint them.
    """

    _log: logging.Logger
    messageQueue: Queue
    threshold_rssi: int
    minimum_interval: float

    def __init__(self, logger: logging.Logger, messageQueue: Queue, minimum_interval: float, threshold_rssi: int):
        self._log = logger
        self.messageQueue = messageQueue
        self.minimum_interval = minimum_interval
        self.threshold_rssi = threshold_rssi
        self.adapter = None
        self.registry = list()

    def __enter__(self):
        self._log.debug("Choosing the first available Bluetooth adapter and "
                        "starting device discovery.")
        self._log.debug("The discovery filter is set to Bluetooth LE only.")
        try:
            self.adapter = find_adapter()
            self.adapter.SetDiscoveryFilter({"Transport": pydbus.Variant("s", "le")})
            self.adapter.StartDiscovery()
        except GLib.Error as ex:
            self._log.exception("Is the bluetooth controller powered on? "
                                "Use `bluetoothctl`, `power on` otherwise.")
            raise ex
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.adapter is not None:
            self._log.debug("Stopping device discovery.")
            self.adapter.StopDiscovery()

        return False

    def run(self):
        """
        Run the Sniffer main loop.
        """
        if self.adapter is not None:
            self._log.debug("Clearing the BlueZ device registry.")
            for path, _ in get_known_devices():
                self.adapter.RemoveDevice(path)

            self._log.debug("Registering the signals InterfacesAdded and PropertiesChanged.")
            bus = pydbus.SystemBus()
            bus.subscribe(
                sender=SERVICE_NAME,
                iface=OBJECT_MANAGER_INTERFACE,
                signal="InterfacesAdded",
                signal_fired=self._cb_interfaces_added
            )
            bus.subscribe(
                sender=SERVICE_NAME,
                iface=OBJECT_MANAGER_INTERFACE,
                signal="InterfacesRemoved",
                signal_fired=self._cb_interfaces_removed
            )
            bus.subscribe(
                sender=SERVICE_NAME,
                iface=PROPERTIES_INTERFACE,
                signal="PropertiesChanged",
                arg0=DEVICE_INTERFACE,
                signal_fired=self._cb_properties_changed
            )

            self._log.debug("Running the main loop.")
            loop = GLib.MainLoop()
            loop.run()
        else:
            raise ValueError("Sniffer.run can only be called in a context "
                             "(e.g. `with Sniffer(...) as s: s.run()`)")

    def _cb_interfaces_added(self, sender, obj, iface, signal, params):
        """
        Upon receiving the InterfacesAdded signal, register any new device.
        """
        self._log.debug("Caught the signal InterfacesAddded.")
        self._log.debug("Added: {}".format(params))
        (path, interfaces) = params
        if DEVICE_INTERFACE in interfaces:
            self._register_device(Device.create_from_dbus_dict(path, interfaces[DEVICE_INTERFACE]))
        if GATT_SERVICE_INTERFACE in interfaces:
            self._register_service(path, interfaces[GATT_SERVICE_INTERFACE])
        if GATT_CHARACTERISTIC_INTERFACE in interfaces:
            self._register_characteristic(path, interfaces[GATT_CHARACTERISTIC_INTERFACE])
        if GATT_DESCRIPTOR_INTERFACE in interfaces:
            self._register_descriptor(path, interfaces[GATT_DESCRIPTOR_INTERFACE])

    def _cb_interfaces_removed(self, sender, obj, iface, signal, params):
        """
        Upon receiving the InterfacesRemoved signal, note the loss of a device.
        """
        self._log.debug("Caught the signal InterfacesRemoved.")
        self._log.debug("Removed: {}".format(params))
        (path, ifaces) = params
        device = self._find_device_by_path(path)
        if device is not None:
            device.active = False
            print_device(device, "Lost")

    def _cb_properties_changed(self, sender, obj, iface, signal, params):
        """
        Upon receiving the PropertiesChanged signal, update previously
        registered devices.
        """
        if DEVICE_INTERFACE in params:
            device = self._find_device_by_path(obj)
            if device is not None:
                if "RSSI" in params[1] and params[1]["RSSI"] > self.threshold_rssi:
                    device.update_from_dbus_dict(obj, params[1])
                    self.addToQueue(device)
            else:
                self._log.debug("Received PropertiesChanged for an "
                                "unknown device.")

    def _register_device(self, device):
        deviceWithinThreshold = device.rssis[-1] > self.threshold_rssi

        d = self._find_device(device)
        if d is not None:
            d.update_from_device(device)
            if deviceWithinThreshold:
                print_device(d, "Merge")
        else:
            self.registry.append(device)
            if deviceWithinThreshold:
                print_device(device, "New")

        if deviceWithinThreshold:
            self.addToQueue(device)

    def _register_service(self, path, service):
        device_path = service["Device"]
        device = self._find_device_by_path(device_path)
        if device is not None:
            device[path] = GATTService(service["UUID"], service["Primary"])
            print_device(device, "Update")
        else:
            self._log.debug("Received a service for an unknown device.")

    def _register_characteristic(self, path, characteristic):
        service_path = characteristic["Service"]
        device_path = "/".join(service_path.split("/")[:-1])
        device = self._find_device_by_path(device_path)
        if device is not None:
            if service_path in device.services:
                device[service_path][path] = GATTCharacteristic(
                    characteristic["UUID"], characteristic.get("Value"),
                    characteristic["Flags"]
                )
                print_device(device, "Characteristic")
            else:
                self._log.debug("Received a characteristic for an unknown service.")
        else:
            self._log.debug("Received a characteristic for an unknown device.")

    def _register_descriptor(self, path, descriptor):
        characteristic_path = descriptor["Characteristic"]
        service_path = "/".join(characteristic_path.split("/")[:-1])
        device_path = "/".join(service_path.split("/")[:-1])
        device = self._find_device_by_path(device_path)
        if device is not None:
            if service_path in device.services:
                if characteristic_path in device[service_path].characteristics:
                    device[service_path][characteristic_path][path] = GATTDescriptor(
                        descriptor["UUID"], descriptor.get("Value"), descriptor.get("Flags")
                    )
                    print_device(device, "Descriptor")
                else:
                    self._log.debug("Received a descriptor for an unknown characteristic.")
            else:
                self._log.debug("Received a descriptor for an unknown service.")
        else:
            self._log.debug("Received a descriptor for an unknown device.")

    def _find_device(self, device) -> Device:
        for d in self.registry:
            if device == d:
                return d

    def _find_device_by_path(self, path) -> Device:
        for d in self.registry:
            if path == d.path:
                return d
            
    def addToQueue(self, device: Device):
        if device.lastSent is None or time.time() - device.getLastSent() > self.minimum_interval:
            identifier: str = device.address
            signal_dbm: float = float(device.rssis[-1])

            # Create grpc message and
            grpc_message = Message(
                identifier=identifier,
                timestamp=time.time(),
                signal_strength=signal_dbm)
            
            device.setLastSent(time.time())

            self.messageQueue.put(grpc_message)
