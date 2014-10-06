# Copyright 2014 Kevin Reid <kpreid@switchb.org>
# 
# This file is part of ShinySDR.
# 
# ShinySDR is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# ShinySDR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with ShinySDR.  If not, see <http://www.gnu.org/licenses/>.

# pylint: disable=dangerous-default-value, no-method-argument
# (no-method-argument: pylint is confused by interfaces)

from __future__ import absolute_import, division

from collections import Counter

from zope.interface import Interface, implements  # available via Twisted

from gnuradio import audio
from gnuradio import blocks
from gnuradio import gr

from shinysdr.signals import SignalType
from shinysdr.types import Range
from shinysdr.values import BlockCell, CollectionState, ExportedState, LooseCell, ViewCell, exported_value, nullExportedState


__all__ = []


class IDevice(Interface):
	'''
	The only implementation of IDevice is Device; it is used only as an explicit type.
	'''
	pass


class IRXDriver(Interface):
	'''
	Additional requirements:
	The object must be a GNU Radio source block with the specified output type.
	get_output_type should be exported.
	'''
	
	def get_output_type():
		'''
		Should return an instance of SignalType describing the output signal.
		
		The value MUST NOT change in an incompatible way during the lifetime of the source. 
		'''

	def get_tune_delay():
		'''
		Return the amount of time, in seconds, between a call to set_freq() and the new center frequency taking effect as observed at top.monitor.fft.
		
		TODO: We need a better strategy for this. Stream tags might help if we can get them in the right places.
		
		TODO: With the device refactoring, tune delays should come from VFOs not rx drivers.
		'''

	def notify_reconnecting_or_restarting():
		pass


__all__.append('IRXDriver')


class ITXDriver(Interface):
	'''
	Additional requirements:
	The object must be a GNU Radio sink block with the specified input type.
	get_input_type should be exported.
	'''

	def get_input_type():
		'''
		Should return an instance of SignalType describing the input signal.
		
		The value MUST NOT change in an incompatible way during the lifetime of the source. 
		'''

	def notify_reconnecting_or_restarting():
		pass


__all__.append('ITXDriver')


class Device(ExportedState):
	'''
	A Device aggregates the functions of one or more pieces of radio hardware or drivers for same; particularly:
	
	* receiver
	* transmitter (not yet implemented)
	* VFO
	
	For example, if one is using a sound card-based transceiver, then there would be an audio-source, an audio-sink, and a separate interface to the VFO and other hardware controls. These are completely unrelated as far as the operating system and GNU Radio are concerned, but the Device object aggregates all of those so that the user interface can display them as properly related and control them in sync.
	'''
	implements(IDevice)

	def __init__(self,
			name=None,
			rx_driver=nullExportedState,
			tx_driver=nullExportedState,
			vfo_cell=None,
			components={}):
		'''
		rx_driver -- may be nullExportedState
		tx_driver -- may be nullExportedState
		vfo_cell -- may be None
		'''
		if vfo_cell is None:
			vfo_cell = _stub_vfo
		assert vfo_cell.key() == 'freq'
		assert isinstance(vfo_cell.type(), Range)
		
		self.__name = name
		self.rx_driver = IRXDriver(rx_driver) if rx_driver is not nullExportedState else nullExportedState
		self.tx_driver = ITXDriver(tx_driver) if tx_driver is not nullExportedState else nullExportedState
		self.__vfo_cell = vfo_cell
		self.__components = components
		self.components = CollectionState(self.__components, dynamic=False)
	
	def get_name(self):
		return self.__name
	
	def state_def(self, callback):
		super(Device, self).state_def(callback)
		callback(self.__vfo_cell)
		callback(BlockCell(self, 'rx_driver'))
		callback(BlockCell(self, 'tx_driver'))
		callback(BlockCell(self, 'components'))
	
	def can_receive(self):
		return self.rx_driver is not nullExportedState
	
	def can_transmit(self):
		return self.tx_driver is not nullExportedState
	
	def can_tune(self):
		return self.__vfo_cell is not _stub_vfo
	
	def get_rx_driver(self):
		return self.rx_driver
	
	def get_tx_driver(self):
		return self.tx_driver
	
	def get_vfo_cell(self):
		return self.__vfo_cell
	
	def get_freq(self):
		'''
		Get the frequency from the VFO cell.
		
		(Convenience/consistency equivalent to self.state()['freq'].get.)
		'''
		return self.__vfo_cell.get()
	
	def set_freq(self, value):
		'''
		Set the frequency in the VFO cell.
		
		(Convenience/consistency equivalent to self.state()['freq'].set.)
		'''
		return self.__vfo_cell.set(value)
	
	def notify_reconnecting_or_restarting(self):
		if self.rx_driver is not nullExportedState:
			self.rx_driver.notify_reconnecting_or_restarting()
		if self.tx_driver is not nullExportedState:
			self.tx_driver.notify_reconnecting_or_restarting()
	
	def get_components(self):
		'''Do not mutate the dictionary returned.'''
		return self.__components


__all__.append('Device')


def _ConstantVFOCell(value):
	value = float(value)
	return LooseCell(
		key='freq',
		value=value,
		ctor=Range([(value, value)]),
		writable=False,
		persists=False)


_stub_vfo = _ConstantVFOCell(0.0)


def merge_devices(devices):
	devices = [IDevice(d) for d in devices]
	if len(devices) == 1:
		return devices[0]
	else:
		names = [d.get_name() for d in devices if d.get_name() is not None]
		rx_drivers = [d.get_rx_driver() for d in devices if d.can_receive()]
		tx_drivers = [d.get_tx_driver() for d in devices if d.can_transmit()]
		vfo_cells = [d.get_vfo_cell() for d in devices if d.can_tune()]
		component_names = Counter(k for d in devices for k in d.get_components().keys())
		merged_components = {}
		for i, d in enumerate(devices):
			if any(component_names[k] > 1 for k in d.get_components().keys()):
				prefix = u'%i-' % i
			else:
				prefix = ''
			for k, component in d.get_components().iteritems():
				merged_components[prefix + k] = component
		return Device(
			name=None if len(names) == 0 else '+'.join(names),
			rx_driver=_at_most_one('RX driver', nullExportedState, rx_drivers),
			tx_driver=_at_most_one('TX driver', nullExportedState, tx_drivers),
			vfo_cell=_merge_vfos(vfo_cells),
			components=merged_components)


__all__.append('merge_devices')


def _at_most_one(name, zero, items):
	if len(items) == 1:
		return items[0]
	elif len(items) == 0:
		return zero
	else:
		raise ValueError(u'Exactly one %s must be provided, not %i' % (name, len(items)))


def _merge_vfos(vfos):
	fixed = 0.0
	variable = []
	for vfo in vfos:
		p = vfo.type().get_single_point()
		if p is not None:
			fixed += p
		else:
			variable.append(vfo)
	if len(variable) == 0:
		if fixed == 0.0:
			return None
		else:
			return _ConstantVFOCell(fixed)
	elif len(variable) == 1:
		variable_one = variable[0]
		if fixed == 0.0:
			return variable_one
		else:
			return ViewCell(
				base=variable_one,
				get_transform=lambda x: x + fixed,
				set_transform=lambda x: x - fixed,
				key='freq',
				ctor=variable_one.type().shifted_by(fixed),
				writable=True,
				persists=variable_one.persists())
	else:
		raise ValueError('Multiple non-stub VFOs not yet supported.')


# ---------------------------------------------------------------------
# Below this point: basic devices.


def FrequencyShift(shift, name=None):
	'''
	Define a fixed VFO frequency shift, such as if a upconverter/downconverter/transverter is in use.
	
	The shift value should be set to the needed change in the _displayed_ frequency. For example, if using a 125 MHz upconverter for receiving HF (such as the popular Ham-It-Up), one should specify a shift of -125e6.
	'''
	shift = float(shift)
	return Device(name=name, vfo_cell=_ConstantVFOCell(shift))


__all__.append('FrequencyShift')


def AudioDevice(
		rx_device='',  # may be used positionally, not recommented
		tx_device=None,
		name=None,
		sample_rate=44100,
		quadrature_as_stereo=False):
	rx_device = str(rx_device)
	if tx_device is not None:
		tx_device = str(tx_device)
		
	if name is None:
		full_name = u'Audio ' + rx_device
		if tx_device is not None:
			full_name += '/' + tx_device
	else:
		full_name = unicode(name)

	rx_driver = _AudioRXDriver(
		device_name=rx_device,
		sample_rate=sample_rate,
		quadrature_as_stereo=quadrature_as_stereo)
	if tx_device is not None:
		tx_driver = _AudioTXDriver(
			device_name=tx_device,
			sample_rate=sample_rate,
			quadrature_as_stereo=quadrature_as_stereo)
	else:
		tx_driver = nullExportedState
	
	return Device(
		name=full_name,
		vfo_cell=LooseCell(
			key='freq',
			value=0.0,
			ctor=Range([(0.0, 0.0)]),
			writable=True,
			persists=False),
		rx_driver=rx_driver,
		tx_driver=tx_driver)


__all__.append('AudioDevice')


class _AudioRXDriver(ExportedState, gr.hier_block2):
	implements(IRXDriver)
	
	def __init__(self,
			device_name,
			sample_rate,
			quadrature_as_stereo):
		self.__device_name = device_name
		self.__sample_rate = sample_rate
		self.__quadrature_as_stereo = quadrature_as_stereo
		
		if self.__quadrature_as_stereo:
			self.__signal_type = SignalType(
				kind='IQ',
				sample_rate=self.__sample_rate)
		else:
			self.__signal_type = SignalType(
				kind='USB',  # TODO obtain correct type from config (or say hamlib)
				sample_rate=self.__sample_rate)
		
		gr.hier_block2.__init__(
			self, type(self).__name__,
			gr.io_signature(0, 0, 0),
			gr.io_signature(1, 1, gr.sizeof_gr_complex * 1),
		)
		
		self.__source = audio.source(
			self.__sample_rate,
			device_name=self.__device_name,
			ok_to_block=True)
		
		combine = blocks.float_to_complex(1)
		self.connect(self.__source, combine, self)
		if self.__quadrature_as_stereo:
			# if we don't do this, the imaginary component is 0 and the spectrum is symmetric
			self.connect((self.__source, 1), (combine, 1))
		# TODO: If not quadrature, we always discard the right channel. Is there a use for it? Would summing mono input reduce noise?
	
	@exported_value(ctor=SignalType)
	def get_output_type(self):
		return self.__signal_type

	def get_tune_delay(self):
		# TODO: Tune delay should be associated with VFOs (or devices) too
		return 0.0
	
	def notify_reconnecting_or_restarting(self):
		pass


class _AudioTXDriver(ExportedState, gr.hier_block2):
	implements(ITXDriver)
	
	def __init__(self,
			device_name,
			sample_rate,
			quadrature_as_stereo):
		self.__device_name = device_name
		self.__sample_rate = sample_rate
		self.__quadrature_as_stereo = quadrature_as_stereo
		
		self.__signal_type = SignalType(
			# TODO: type should be able to be LSB
			kind='IQ' if quadrature_as_stereo else 'USB',
			sample_rate=self.__sample_rate)
		
		gr.hier_block2.__init__(
			self, type(self).__name__,
			gr.io_signature(1, 1, gr.sizeof_gr_complex * 1),
			gr.io_signature(0, 0, 0),
		)
		
		sink = audio.sink(
			self.__sample_rate,
			device_name=self.__device_name,
			ok_to_block=True)
		
		split = blocks.complex_to_float(1)
		self.connect(self, split, (sink, 0))
		self.connect((split, 1), (sink, 1))

	@exported_value(ctor=SignalType)
	def get_input_type(self):
		return self.__signal_type

	def get_tune_delay(self):
		# TODO: Tune delay should be associated with VFOs (or devices) too
		return 0.0
	
	def notify_reconnecting_or_restarting(self):
		pass


def PositionedDevice(latitude, longitude):
	'''
	Combine with other devices to specify a device's location on the Earth.
	'''
	return Device(components={'position': _PositionedDeviceComponent(latitude, longitude)})


class IPositionedDevice(Interface):
	'''
	Client marker interface only.
	'''


class _PositionedDeviceComponent(ExportedState):
	implements(IPositionedDevice)
	
	def __init__(self, latitude, longitude):
		self.__position = float(latitude), float(longitude)

	@exported_value()
	def get_position(self):
		return self.__position
