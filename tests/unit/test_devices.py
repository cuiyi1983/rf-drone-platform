"""
test_devices.py - Unit tests for devices.py
"""

import numpy as np
import pytest

from collector.devices import (
    DEVICE_IMPL,
    DeviceCapabilities,
    DeviceInfo,
    IDevice,
    MockPlutoDevice,
    connect_device,
    discover_devices,
    get_device_class,
)


class TestDeviceInfo:
    def test_device_info_creation(self):
        d = DeviceInfo(
            id="usb:2.6.5",
            type="pluto",
            name="ADALM PLUTO",
            connected=True,
            fw_version="v0.34",
            temperature=45.5,
        )
        assert d.id == "usb:2.6.5"
        assert d.type == "pluto"
        assert d.connected is True
        assert d.temperature == 45.5


class TestDeviceCapabilities:
    def test_defaults(self):
        caps = DeviceCapabilities()
        assert caps.frequency_range == (325_000_000, 6_000_000_000)
        assert caps.buffer_size_range == (1024, 1_048_576)
        assert caps.gain_range == (0.0, 60.0)
        assert caps.sample_rate_fixed == 60_000_000
        assert caps.rf_bandwidth_fixed == 56_000_000
        assert caps.default_frequency == 5_805_000_000
        assert caps.default_buffer_size == 524_288
        assert caps.default_gain == 20.0


class TestMockPlutoDevice:
    """Tests run against MockPlutoDevice – no real hardware required."""

    def test_discover_returns_list(self):
        infos = MockPlutoDevice.discover()
        assert isinstance(infos, list)
        assert len(infos) >= 1
        assert all(isinstance(d, DeviceInfo) for d in infos)

    def test_discover_returns_expected_fields(self):
        infos = MockPlutoDevice.discover()
        for d in infos:
            assert d.id
            assert d.type == "pluto"
            assert d.name == "ADALM PLUTO"

    def test_connect_returns_instance(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        assert isinstance(dev, MockPlutoDevice)
        assert dev.is_connected() is True

    def test_disconnect(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        dev.disconnect()
        assert dev.is_connected() is False

    def test_set_frequency(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        dev.set_frequency(2_450_000_000)
        # No error means success
        assert True

    def test_set_gain(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        dev.set_gain(30.0)
        assert True

    def test_set_buffer_size(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        dev.set_buffer_size(262_144)
        assert True

    def test_read_samples_returns_bytes(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        data = dev.read_samples(1024)
        assert isinstance(data, bytes)
        # 1024 complex samples = 2048 floats
        assert len(data) == 1024 * 2 * 4  # float32 = 4 bytes

    def test_read_samples_default_uses_buffer_size(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        dev.set_buffer_size(4096)
        data = dev.read_samples()
        assert len(data) == 4096 * 2 * 4

    def test_get_temperature(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        temp = dev.get_temperature()
        assert isinstance(temp, float)
        assert temp > 0

    def test_get_device_info(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        info = dev.get_device_info()
        assert info.id == "usb:2.6.5"
        assert info.type == "pluto"
        assert info.fw_version == "v0.34"

    def test_get_capabilities(self):
        dev = MockPlutoDevice.connect("usb:2.6.5")
        caps = dev.get_capabilities()
        assert caps.sample_rate_fixed == 60_000_000
        assert caps.default_gain == 20.0


class TestModuleLevel:
    def test_get_device_class_mock(self, monkeypatch):
        # Force mock impl
        monkeypatch.setattr("collector.devices.DEVICE_IMPL", "mock")
        # Re-import to pick up patched value (devices module already loaded)
        import collector.devices as dev_module
        dev_module.DEVICE_IMPL = "mock"
        cls = dev_module.get_device_class()
        assert cls is MockPlutoDevice

    def test_connect_device(self, monkeypatch):
        import collector.devices as dev_module
        dev_module.DEVICE_IMPL = "mock"
        dev = dev_module.connect_device("usb:2.6.5")
        assert dev.is_connected() is True

    def test_discover_devices(self, monkeypatch):
        import collector.devices as dev_module
        dev_module.DEVICE_IMPL = "mock"
        infos = dev_module.discover_devices()
        assert isinstance(infos, list)
        assert len(infos) >= 1