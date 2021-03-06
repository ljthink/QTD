#!/usr/bin/python3

# Support for the Texas Instruments tdc7201 time-to-digital converter chip.
# Web page: http://www.ti.com/product/tdc7201
#
# This code assumes that you are working with the TDC7201-ZAX-EVM
# evaluation board. For the most part, things won't change for the raw chip,
# Differences will be noted where appropriate.
#
# This code requires SPI to talk to the chip and set control registers and
# read result registers. SPI must be turned on at the kernel level.
# You can do this in Preferences -> Raspberry Pi Configuration -> Interfaces;
# it will require a reboot. If it is on, there should be an uncommented line
# like "dtparam=spi=on" in /boot/config.txt.
# Written and tested on a Raspberry Pi 3.

import RPi.GPIO as GPIO
# Needs to be 0.5.1 or later for interrupts to work.
# This code was written and tested on 0.6.3.
# print("GPIO version =", GPIO.VERSION)
import spidev
import time
# sys for exit()
import sys
# random for creating stimuli for testing
import random

# Map of EVM board header pinout.
# "." means No Connect, parentheses mean probably optional.
#      +3.3V  1	 21 .		 (DTG_TRIG) 40	20 GND
#     ENABLE  2  22 GND		(MSP_START) 39	19 TRIG1
#          .  3  23 .		        CS2 38 	18 CS1
#          .  4  24 .		      TRIG2 37	17 .
# OSC_ENABLE  5  25 .		          . 36	16 .
#          .  6  26 .		          . 35	15 DIN
#       SCLK  7  27 .		     (SCLK) 34	14 DOUT1
#          .  8  28 .		          . 33	13 INT1
#      DOUT2  9  29 .		          . 32	12 INT2
#      (DIN) 10  30 .		          . 31	11 .


class TDC7201():
    # Chip registers and a few combinations of registers
    REGNAME = [	"CONFIG1",			# 0x00
                "CONFIG2",			# 0x01
                "INT_STATUS",			# 0x02
                "INT_MASK",			# 0x03
                "COARSE_CNTR_OVF_H",		# 0x04
                "COARSE_CNTR_OVF_L",		# 0x05
                "CLOCK_CNTR_OVF_H",		# 0x06
                "CLOCK_CNTR_OVF_L",		# 0x07
                "CLOCK_CNTR_STOP_MASK_H",	# 0x08
                "CLOCK_CNTR_STOP_MASK_L",	# 0x09
                "COARSE_CNTR_OVF",		# 0x0A (combination)
                "CLOCK_CNTR_OVF",		# 0x0B (combination)
                "CLOCK_CNTR_STOP_MASK",		# 0x0C (combination)
                "",				# 0x0D
                "",				# 0x0E
                "",				# 0x0F
                "TIME1",			# 0x10
                "CLOCK_COUNT1",			# 0x11
                "TIME2",			# 0x12
                "CLOCK_COUNT2",			# 0x13
                "TIME3",			# 0x14
                "CLOCK_COUNT3",			# 0x15
                "TIME4",			# 0x16
                "CLOCK_COUNT4",			# 0x17
                "TIME5",			# 0x18
                "CLOCK_COUNT5",			# 0x19
                "TIME6",			# 0x1A
                "CALIBRATION1",			# 0x1B
                "CALIBRATION2"			# 0x1C
              ]

    # PIN DEFINITIONS
    # +3.3V and *both* GND pins need to be hardwired,
    # and SPI signals need to be handled by spidev,
    # but everything else has to be assigned to a GPIO pin.
    # The assignments are arbitrary but must match hardware wiring.
    # Here we use only GPIO with no "alternate function",
    # for maximum flexibility.
    #
    # ENABLE high turns chip on, ENABLE low forces complete reset.
    ENABLE = 12	# GPIO 18 = header pin 12 (arbitrary)
    #
    # OSC_ENABLE turns on the EVM's clock generator chip.
    # It is a board function, not a chip function.
    # You don't need it if you are supplying an external clock.
    OSC_ENABLE = 16	# GPIO 23 = header pin 16 (arbitrary)
    #
    # The TRIGx signal indicates that the converter is ready to start.
    # Typically you would either use it to directly trigger some hardware,
    # or wait for it in your processor before starting a measurement.
    # We assume the latter here, so assign a pin to it.
    # Note that measurement does not start until converter gets the START signal.
    # The INTx signal indicates that measurement has stopped.
    # This can be because of success, or timeout; you need to read INT_STATUS
    # to determine which.
    # TRIG1 and INT1 only need to be set if you are using the #1 side of chip.
    TRIG1 = 7	# GPIO  4 = header pin  7  (arbitrary)
    INT1 = 37	# GPIO 26 = header pin 37 (arbitrary)
    # TRIG2 and INT2 only need to be set if you are using the #2 side of chip.
    TRIG2 = 11	# GPIO 17 = header pin 11 (arbitrary)
    INT2 = 32	# GPIO 12 = header pin 32 (arbitrary)

    # The START signal may be supplied externally to each side,
    # separately through START_EXT1 and START_EXT2 SMA connectors,
    # or together through the COMMON_START SMA connector.
    # If you want to drive one of these from the RPi, then the
    # MSP_START signal needs to be wired up to TP10, a PCB mount SMA connector
    # needs to be soldered into the EVM board at TP10, you need to put
    # a cable from TP10 to one of the START SMA connectors mentioned above,
    # and the GPIO needs to be declared.
    START = 18	# GPIO 24 = header pin 18 (arbitrary)
    # For STOP, there is no space on the EVM.
    # Testing STOP requires a connector on the support board,
    # wired to the RPi. (Or a real signal.)
    STOP = 22	# GPIO 25 = header pin 22 (arbitrary)

    # SPI pins are owned by spidev and must NOT be set using GPIO lib.
    SCLK = 23	# GPIO 11 = header pin 23 is hardwired for SPI0 SCLK
    CS1 = 24	# GPIO  8 = header pin 24 is hardwired for SPI0 CS0
    CS2 = 26	# GPIO  7 = header pin 26 is hardwired for SPI0 CS1
    MOSI = 19	# DIN	# GPIO 10 = header pin 19 is hardwired for SPI0 MOSI
    MISO = 21	# DOUT1	# GPIO  9 = header pin 21 is hardwired for SPI0 MISO
    # NOTE that chip CS1 and CS2 are wired to Pi3 CS0 and CS1 respectively!
    # Don't get confused and think that Pi3 CS1 drives chip CS1! It doesn't!
    # DOUT2 = MISO2 can be shorted to DOUT1 = MISO1, as long as
    # CS1 and CS2 are *NEVER* both asserted low at the same time
    # but I'm not sure what spidev does about that, so leave it separate.
    # The EVM brings it out separately.
    # Note that we CANNOT read side #2 of the chip in this configuration.
    # Don't need DOUT2 if only using side #1, but assign for completenes.
    #DOUT2 = 15	# GPIO  22 = header pin 15 (arbitrary)
 
    # TDC register address bit masks
    _AI = 0x80		# 0b10000000
    _WRITE = 0x40	# 0b01000000
    _ADDRESS = 0x3F	# 0b00111111
    #
    # TDC7201 register addresses - 8-bit
    # These can be read or written.
    CONFIG1			= 0x00
    CONFIG2			= 0x01
    INT_STATUS			= 0x02
    INT_MASK			= 0x03
    COARSE_CNTR_OVF_H		= 0x04
    COARSE_CNTR_OVF_L		= 0x05
    CLOCK_CNTR_OVF_H		= 0x06
    CLOCK_CNTR_OVF_L		= 0x07
    CLOCK_CNTR_STOP_MASK_H	= 0x08
    CLOCK_CNTR_STOP_MASK_L	= 0x09
    MINREG8			= 0x00
    MAXREG8			= 0x09
    #REG8RANGE			= range(MINREG8,MAXREG8+1)
    # Not actual chip registers, but 16-bit pairs of 8
    COARSE_CNTR_OVF		= 0x0A
    CLOCK_CNTR_OVF		= 0x0B
    CLOCK_CNTR_STOP_MASK	= 0x0C
    #
    # CONFIG1 register bit masks
    # Calibrate after every measurement, even if interrupted?
    _CF1_FORCE_CAL		= 0b10000000
    # Add a parity bit (even parity) for 24-bit registers?
    _CF1_PARITY_EN		= 0b01000000
    # Invert TRIGG signals (falling edge instead of rising edge)?
    _CF1_TRIGG_EDGE		= 0b00100000
    # Inverted STOP signals (falling edge instead of rising edge)?
    _CF1_STOP_EDGE		= 0b00010000
    # Inverted START signals (falling edge instead of rising edge)?
    _CF1_START			= 0b00001000
    # Neasurememnt mode 1 or 2? (Other values reserved.)
    _CF1_MEAS_MODE		= 0b00000110
    _CF1_MM1			= 0b00000000
    _CF1_MM2			= 0b00000010
    # Start new measurement
    # Automagically starts a measurement.
    # Is automagically cleared when a measurement is complete.
    # DO NOT poll this to see when a measurement is done!
    # Use the INT1 (or INT2) signal instead.
    _CF1_START_MEAS		= 0b00000001
    #
    # CONFIG2 register bit masks
    # Number of calibration periods
    _CF2_CALIBRATION_PERIODS	= 0b11000000
    _CF2_CAL_PERS_2		= 0b00000000
    _CF2_CAL_PERS_10		= 0b01000000	# default on reset
    _CF2_CAL_PERS_20		= 0b10000000
    _CF2_CAL_PERS_40		= 0b11000000
    # Number of cycles to average over
    _CF2_AVG_CYCLES		= 0b00111000
    _CF2_AVG_CYC_1		= 0b00000000	# no averaging, default
    _CF2_AVG_CYC_2		= 0b00001000
    _CF2_AVG_CYC_4		= 0b00010000
    _CF2_AVG_CYC_8		= 0b00011000
    _CF2_AVG_CYC_16		= 0b00100000
    _CF2_AVG_CYC_32		= 0b00101000
    _CF2_AVG_CYC_64		= 0b00110000
    _CF2_AVG_CYC_128		= 0b00111000
    # Number of stop pulses to wait for.
    _CF2_NUM_STOP		= 0b00000111
    _CF2_NSTOP_1		= 0b00000000	# default on reset
    _CF2_NSTOP_2		= 0b00000001
    _CF2_NSTOP_3		= 0b00000010
    _CF2_NSTOP_4		= 0b00000011
    _CF2_NSTOP_5		= 0b00000100
    #
    # INT_STATUS register bit masks
    # Upper 3 bits are reserved.
    # Writing a 1 to any of the other bits should clear their status.
    # Did the measurement complete?
    _IS_COMPLETE		= 0b00010000
    # Has the measurement started?
    _IS_STARTED			= 0b00001000
    # Did the clock overflow?
    _IS_CLOCK_OVF		= 0b00000100
    # Did the coarse counter overflow?
    _IS_COARSE_OVF		= 0b00000010
    # Was an interrupt generated?
    # May be identical information to _IS_COMPLETE.
    _IS_INTERRUPT		= 0b00000001
    #
    # INT_MASK register bit masks
    # Upper 5 bits are reserved.
    # Is the clock counter overflow enabled?
    _IM_CLOCK_OVF	= 0b00000100
    # Is the coarse counter overflow enabled?
    _IM_COARSE_OVF	= 0b00000010
    # Is the measurement complete interrupt enabled?
    _IM_INTERRUPT	= 0b00000001
    #
    # TDC7201 register addresses - 24-bit
    # These can be read but usually should not be written,
    # as they contain results of measurement or calibration.
    TIME1			= 0x10
    CLOCK_COUNT1		= 0x11
    TIME2			= 0x12
    CLOCK_COUNT2		= 0x13
    TIME3			= 0x14
    CLOCK_COUNT3		= 0x15
    TIME4			= 0x16
    CLOCK_COUNT4		= 0x17
    TIME5			= 0x18
    CLOCK_COUNT5		= 0x19
    TIME6			= 0x1A
    CALIBRATION1		= 0x1B
    CALIBRATION2		= 0x1C
    MINREG24			= 0x10
    MAXREG24			= 0x1C
    #REG24RANGE			= range(MINREG24,MAXREG24+1)
    #REGRANGE			= range(0,MAXREG24+1)
    # Note that side #1 and #2 of the chip EACH have a full set of registers!
    # Which one you are talking to depends on the chip select!
    # Within spidev, you need to close one side and then open the other to switch.

    # TDC7201 clock, for calculating actual TOFs
    clockFrequency = 8000000		# usually 8 MHz, though 16 is better
    clockPeriod = 1.0 / clockFrequency	# usually 125 nS

    _minSPIspeed = 50000
    _maxSPIspeed = 25000000


    def __init__(self):
        # Instance variables
        self._spi = spidev.SpiDev()
        self.reg1 = [None for i in range(self.MAXREG24+1)]
        #self.reg2 = [None for i in range(self.MAXREG24+1)]
        # Open SPI to side 1 of the chip
        # Later we should write routines 
        self._spi.open(0,0)	# Open SPI port 0, RPi CS0 = chip CS1.
        self.chip_select = 1
        #print("SPI interface started to tdc7201 side 1.")
        # Check all SPI settings.
        #print("Default SPI settings:")
        #print("SPI bits per word (should be 8):", self._spi.bits_per_word)
        if (self._spi.bits_per_word != 8):
            print("Setting bits per word to 8")
            self._spi.bits_per_word = 8
        #print("SPI chip select high? (should be False):", self._spi.cshigh)
        if (self._spi.cshigh):
            print("Setting chip selects to active low")
            self._spi.cshigh = False
        #print("SPI loopback? (should be False):", self._spi.loop)
        if (self._spi.loop):
            print("Setting loopback to False")
            self._spi.loop = False
        #print("SPI LSB-first? (should be False):", self._spi.lsbfirst)
        if (self._spi.lsbfirst):
            print("Setting bit order to MSB-first")
            self._spi.lsbfirst = False
        #print("SPI default clock speed:", self._spi.max_speed_hz)
        #print("SPI mode (CPOL|CPHA) (should be 0b00):", self._spi.mode)
        if (self._spi.mode != 0):
            print("Setting polarity and phase to normal")
            self._spi.mode = 0
        #print("SPI threewire? (should be False):", self._spi.threewire)
        if (self._spi.threewire):
            print("Setting 3-wire to False")
            self._spi.threewire = False

    def initGPIO(self):
        GPIO.setmode(GPIO.BOARD)	# Use header pin numbers, not GPIO numbers.
        GPIO.setwarnings(False) 
        #print("Initializing Raspberry Pi pin directions for tdc7201 driver.")
        print("Initializing tdc7201 driver.")

        # Initialize the ENABLE pin to low, which resets the tdc7201.
        GPIO.setup(self.ENABLE,GPIO.OUT,initial=GPIO.LOW)
        # We need to hold reset for a bit, so remember when we started.
        reset_start = time.time()
        #print("Reset asserted (ENABLE = low) on pin", self.ENABLE, ".")

        # Both chip selects must start out HIGH (inactive).
        # Not sure we can do that through GPIO lib though.
        # For intial tests, disable side 2.
        #GPIO.setup(self.CS1,GPIO.OUT,initial=GPIO.HIGH)
        # Permanently (?) turn off SPI on side #2.
        #GPIO.setup(self.CS2,GPIO.OUT,initial=GPIO.HIGH)

        # Start the on-board clock generator running.
        # May not be necessary if you supply an external clock.
        GPIO.setup(self.OSC_ENABLE,GPIO.OUT,initial=GPIO.HIGH)
        #print("Clock started (OSC_ENABLE = high) on pin", self.OSC_ENABLE, ".")

        # Set up TRIG1 pin to know when chip is ready to measure.
        GPIO.setup(self.TRIG1,GPIO.IN)
        #print("Set TRIG1 to input on pin", self.TRIG1, ".")
        # Set up INT1 pin for interrupt-driven reads from tdc7201.
        GPIO.setup(self.INT1,GPIO.IN)
        #print("Set INT1 to input on pin", self.INT1, ".")

        # We're not using side #2 of the chip so far, but we wired these.
        # Set up TRIG2 pin to know when chip is ready to measure.
        GPIO.setup(self.TRIG2,GPIO.IN)
        #print("Set TRIG2 to input on pin", self.TRIG2, ".")
        # Set up INT2 pin for interrupt-driven reads from tdc7201.
        GPIO.setup(self.INT2,GPIO.IN)
        #print("Set INT2 to input on pin", self.INT2, ".")

        # Set up START and STOP, initially inactive.
        GPIO.setup(self.START,GPIO.OUT,initial=GPIO.LOW)
        #print("Set START to output (low) on pin", self.START, ".")
        GPIO.setup(self.STOP,GPIO.OUT,initial=GPIO.LOW)
        #print("Set STOP to output (low) on pin", self.STOP, ".")

    def off(self):
        print("Turning off tdc7201.")
        if self.chip_select:
            self._spi.close()
            self.chip_select = 0
        GPIO.output(self.ENABLE,GPIO.LOW)
        time.sleep(0.1)

    def on(self):
        now = time.time()
        print("tdc7201 enabled at", now)
        # Turn on chip enable.
        GPIO.output(self.ENABLE,GPIO.HIGH)
        # Wait 10 mS for chip to settle.
        # Data sheet says only 1.5 mS required, and
        # SPI available sooner, but we're not in a hurry.
        time.sleep(0.01)

        # Set measurement mode 2.
        print("Setting measurement mode 2 with forced calibration.")
        cf1_state = self._CF1_FORCE_CAL | self._CF1_MM2
        self.write8(self.CONFIG1, cf1_state)
        # Read it back to make sure.
        result = self.read8(self.CONFIG1)
        if (result != cf1_state):
            print("Couldn't set CONFIG1.")
            print(self.REGNAME[self.CONFIG1], ":", result, "=", hex(result), "=", bin(result))
            print("Desired state:", cf1_state, "=", hex(cf1_state), "=", bin(cf1_state))
            if (result == 0):
                print("Are you sure the TDC7201 is connected to the Pi's SPI interface?")
            self._spi.close()
            sys.exit()
        # Change calibration periods from 2 to 40, and 3 stops.
        print("Setting 40-clock-period calibration and 3 stop pulses.")
        cf2_state = self._CF2_CAL_PERS_40 | self._CF2_NSTOP_3
        self.write8(self.CONFIG2, cf2_state)
        # Read it back to make sure.
        result = self.read8(self.CONFIG2)
        if (result != cf2_state):
            print("Couldn't set CONFIG2.")
            print(self.REGNAME[self.CONFIG2], ":", result, "=", hex(result), "=", bin(result))
            print("Desired state:", cf2_state, "=", hex(cf2_state), "=", bin(cf2_state))
            self._spi.close()
            sys.exit()
        # Set STOP mask to skip a STOP pulse immediately after a START.
        sm = 0x01
        print("Skipping STOP pulses for", sm, "clock periods =", sm*self.clockPeriod, "S")
        self.write8(self.CLOCK_CNTR_STOP_MASK_H, 0)	# default, but make sure
        self.write8(self.CLOCK_CNTR_STOP_MASK_L, sm)
        result = (self.read8(self.CLOCK_CNTR_STOP_MASK_H) << 8) | self.read8(self.CLOCK_CNTR_STOP_MASK_L)
        if (result != sm):
            print("Couldn't set CLOCK_CNTR_STOP_MASK.")
            print(self.REGNAME[self.CLOCK_CNTR_STOP_MASK], ":", result)
            print("Desired state:", sm, "=", hex(sm), "=", bin(sm))
            self._spi.close()
            sys.exit()
        # Set timeout to 22 uS = 10 muon mean lifetimes.
        #timeout = 0.000022
        # Set timeout to 500 uS for testing
        timeout = 0.0005
        overflowCycles = int(timeout / self.clockPeriod) + 1
        print("Setting timeout to", 1000000*timeout, "uS =", overflowCycles, "clock periods.")
        self.write8(self.CLOCK_CNTR_OVF_H, overflowCycles >> 8)
        self.write8(self.CLOCK_CNTR_OVF_L, overflowCycles & 0xff)
        result = (self.read8(self.CLOCK_CNTR_OVF_H) << 8) | self.read8(self.CLOCK_CNTR_OVF_L)
        if (result != overflowCycles):
            print("Couldn't set CLOCK_CNTR_OVF.")
            print(self.REGNAME[self.CLOCK_CNTR_OVF], ":", result)
            print("Desired state:", overflowCycles)
            self._spi.close()
            sys.exit()

    def write8(self,reg,val):
        assert (reg >= self.MINREG8) and (reg <= self.MAXREG8)
        result = self._spi.xfer([reg|self._WRITE, val&0xFF])

    def read8(self,reg):
        assert (reg >= self.MINREG8) and (reg <= self.MAXREG8)
        result = self._spi.xfer([reg, 0x00])
        return result[1]

    def read24(self,reg):
        assert (reg >= self.MINREG24) and (reg <= self.MAXREG24)
        result = self._spi.xfer([reg, 0x00, 0x00, 0x00])
        # data is MSB-first
        return (result[1] << 16) | (result[2] << 8) | result[3]

    # Read all chip registers
    def read_regs1(self):
        # This might be faster using the auto-increment feature.
        # Leave that for a future performance enhancement.
        # Read 8-bit registers.
        for r in range(self.MINREG8,self.MAXREG8+1):
            self.reg1[r] = self.read8(r)
            #print(r, self.reg1[r])
        self.reg1[self.COARSE_CNTR_OVF] = (self.reg1[self.COARSE_CNTR_OVF_H] << 8) | self.reg1[self.COARSE_CNTR_OVF_L]
        self.reg1[self.CLOCK_CNTR_OVF] = (self.reg1[self.CLOCK_CNTR_OVF_H] << 8) | self.reg1[self.CLOCK_CNTR_OVF_L]
        self.reg1[self.CLOCK_CNTR_STOP_MASK] = (self.reg1[self.CLOCK_CNTR_STOP_MASK_H] << 8) | self.reg1[self.CLOCK_CNTR_STOP_MASK_L]
        # Read 24-bit registers.
        for r in range(self.MINREG24,self.MAXREG24+1):
            self.reg1[r] = self.read24(r)
            #print(r, self.reg1[r])

    def print_regs1(self):
        for r in range(self.MINREG8,self.MAXREG8+1):
            print(self.REGNAME[r], hex(self.reg1[r]))
        # Use combined registers for brevity.
        for r in range(self.COARSE_CNTR_OVF,self.CLOCK_CNTR_STOP_MASK+1):
            print(self.REGNAME[r], self.reg1[r])
        for r in range(self.MINREG24,self.MAXREG24+1):
            print(self.REGNAME[r], self.reg1[r])

    def tof_mm2(self,time1,time2,count):
        # Compute time-of-flight given Measurement Mode 2 data for two adjacent stops.
        if (self.reg1[time2] or self.reg1[count]):
            return self.normLSB*(self.reg1[time1]-self.reg1[time2]) + self.reg1[count]*self.clockPeriod
        else:
            return 0

    # Check if we got any pulses and calculate the TOFs.
    def compute_TOFs(self):
        #print("Computing TOFs.")
        # Check measurement mode.
        self.meas_mode = self.reg1[self.CONFIG1] & self._CF1_MEAS_MODE
        assert (self.meas_mode == self._CF1_MM2)	# We only know MM2 so far.
        #print("Measurement mode 2")
        # Determine number of calibration periods.
        cal_per_code = self.reg1[self.CONFIG2] & self._CF2_CALIBRATION_PERIODS
        if (cal_per_code == self._CF2_CAL_PERS_40):
            self.cal_pers = 40
        elif (cal_per_code == self._CF2_CAL_PERS_20):
            self.cal_pers = 20
        elif (cal_per_code == self._CF2_CAL_PERS_10):
            self.cal_pers = 10
        else:	# == _CF2_CAL_PERS_2
            self.cal_pers = 2
        #print("Calibration periods:", self.cal_pers)
        self.calCount = (self.reg1[self.CALIBRATION2] - self.reg1[self.CALIBRATION1]) / (self.cal_pers -1)
        #print("calCount:", self.calCount)
        if (self.calCount == 0):
            print("No calibration, therefore can't compute timing.")
            return 6	# No calibration, therefore can't compute timing.
        self.normLSB = self.clockPeriod / self.calCount
        #print("clockPeriod:", self.clockPeriod)
        #print("normLSB:", self.normLSB)
        pulses = 0
        self.TOF1 = self.tof_mm2(self.TIME1,self.TIME2,self.CLOCK_COUNT1)
        pulses += bool(self.TOF1)
        self.TOF2 = self.tof_mm2(self.TIME2,self.TIME3,self.CLOCK_COUNT2)
        pulses += bool(self.TOF2)
        self.TOF3 = self.tof_mm2(self.TIME3,self.TIME4,self.CLOCK_COUNT3)
        pulses += bool(self.TOF3)
        self.TOF4 = self.tof_mm2(self.TIME4,self.TIME5,self.CLOCK_COUNT4)
        pulses += bool(self.TOF4)
        self.TOF5 = self.tof_mm2(self.TIME5,self.TIME6,self.CLOCK_COUNT5)
        pulses += bool(self.TOF5)
        log_line = "P " + str(pulses)
        if (pulses >= 2):
            log_line += " "
            log_line += str(self.TOF2 - self.TOF1)
        if (pulses >= 3):
            log_line += " "
            log_line += str(self.TOF3 - self.TOF2)
        if (pulses >= 4):
            log_line += " "
            log_line += str(self.TOF4 - self.TOF3)
        if (pulses >= 5):
            log_line += " "
            log_line += str(self.TOF5 - self.TOF4)
        #if (pulses >= 2):
        #    print(log_line)
        #print(pulses, "pulses detected")
        #if (pulses >= 2):
        #    print("Decay time =", (self.TOF2 - self.TOF1))
        return pulses

    def clear_status(self,verbose=False,force=False):
        # Clear interrupt register bits to prepare for next measurement.
        if verbose:
            print("Checking interrupt status register.")
        int_stat = self.read8(self.INT_STATUS)
        if verbose:
            print("Status was", bin(int_stat))
        if (int_stat or force):
            # Write a 1 to each set bit to clear it.
            self.write8(self.INT_STATUS,int_stat)
            if verbose:
                print("After clearing we got", bin(self.read8(self.INT_STATUS)))
        else:
            if verbose:
                print("No need to clear.")

    def measure(self,simulate=False):
        meas_start = time.time()
        # Check GPIO state doesn't indicate a measurement is happening.
        if (not GPIO.input(self.INT1)):
            print("WARNING: INT1 already active (low).")
            # Try to fix it
            self.clear_status(verbose=True)
            return 13
        if (GPIO.input(self.TRIG1)):
            print("ERROR: TRIG1 already active (high).")
            return 12
        # To start measurement, need to set START_MEAS in TDCx_CONFIG1 register.
        # First read current value.
        cf1 = self.read8(self.CONFIG1)
        # Check it's not already set.
        if (cf1 & self._CF1_START_MEAS):
            print("ERROR: CONFIG1 already has START_MEAS bit set.")
            return 11
        #print("Starting measurement.")
        self.write8(self.CONFIG1,cf1|self._CF1_START_MEAS)
        # Wait for TRIG1. This is inefficient, but OK for now.
        timeout = time.time() + 0.1	#Don't wait longer than a tenth of a second.
        while (not GPIO.input(self.TRIG1)) and (time.time() < timeout):
            pass
        if GPIO.input(self.TRIG1):
            #print("Got trigger, issuing start.")
            pass
        else:
            print("ERROR: Timed out waiting for trigger to rise.")
            return 10
        # Check that INT1 is inactive (high) as expected.
        if GPIO.input(self.INT1):
            #print("INT1 is inactive (high) as expected.")
            pass
        else:
            print("ERROR: INT1 is active (low)!")
            return 9
        # Last chance to check registers before sending START pulse?
        #print(tdc.REGNAME[tdc.CONFIG1], ":", hex(tdc.read8(tdc.CONFIG1)))
        #print(tdc.REGNAME[tdc.CONFIG2], ":", hex(tdc.read8(tdc.CONFIG2)))
        # We got a trigger, so issue a START pulse.
        GPIO.output(self.START,GPIO.HIGH)
        #time.sleep(0.000001)
        GPIO.output(self.START,GPIO.LOW)
        #print("Generated START pulse.")
        #if GPIO.input(self.INT1):
        #    #print("INT1 is inactive (high) as expected.")
        #    pass
        #else:
        #    print("ERROR: INT1 is active (low)!")
        #    return
        # Wait for TRIG1 to fall. This is inefficient, but OK for now.
        timeout = time.time() + 0.1	#Don't wait longer than a tenth of a second.
        while (GPIO.input(self.TRIG1)) and (time.time() < timeout):
            pass
        if GPIO.input(self.TRIG1):
            print("Timed out waiting for trigger to fall.")
            return 8
        else:
            #print("TRIG1 fell as expected.")
            pass
        if simulate:
            # Send 0 to 4 STOP pulses. FOR TESTING ONLY.
            threshold = 2.0/4.0
            for p in range(4):
                if random.random() < threshold:
                    GPIO.output(self.STOP,GPIO.HIGH)
                    GPIO.output(self.STOP,GPIO.LOW)
            #if GPIO.input(self.INT1):
            #    print("INT1 is inactive (high) as expected.")
            #else:
            #    print("ERROR: INT1 is active (low)!")
            #    return
        # Wait for INT1. This is inefficient, but OK for now.
        # INT1 is active low.
        timeout = time.time() + 0.1	#Don't wait longer than a tenth of a second.
        while ((GPIO.input(self.INT1)) and (time.time() < timeout)):
            pass
        if GPIO.input(self.INT1):
            print("Timed out waiting for INT1.")
            return 7
        else:
            #print("Got measurement-complete interrupt.")
            pass
        # Read everything in and see what we got.
        #print("Reading chip side #1 register state:")
        self.read_regs1()
        #self.print_regs1()
        returnCode = self.compute_TOFs()
        #meas_end = time.time()
        #print("Measurement took", meas_end-meas_start, "S.")
        #self.clear_status()	# clear interrupts
        return returnCode # 0-5 for number of pulses (< NSTOP implies timeout), 6 for error

    def set_SPI_clock_speed(self,speed):
        # Lower than this may work but is kind of silly.
        if (speed < self._minSPIspeed):
            print("WARNING: SPI clock speed", speed, "is too low, using", self._minSPIspeed, "instead.")
            speed = self._minSPIspeed
        # 25 MHz is max for the chip, but probably Rpi can't set that exactly.
        # Highest RPi option below that may 15.6 MHz.
        if (speed > self._maxSPIspeed):
            print("WARNING: SPI clock speed", speed, "is too high, using", self._maxSPIspeed, "instead.")
            speed = self._maxSPIspeed
        print("Setting SPI clock speed to", speed/1000000, "MHz.")
        self._spi.max_speed_hz = speed

# Execute if called as a program (and not if imported as a library)
if __name__ == "__main__":
    tdc = TDC7201()	# Create TDC object with SPI interface.
    tdc.initGPIO()	# Set pin directions and default values for non-SPI signals.

    # Setting and checking clock speed.
    tdc.set_SPI_clock_speed(25000000)
    #print("")

    # Internal timing
    #print("UNIX time settings:")
    #print("Epoch (time == 0):", time.asctime(time.gmtime(0)))
    now = time.time()
    #print("Time since epoch in seconds:", now)
    #print("Current time (UTC):", time.asctime(time.gmtime(now)))
    print("Current time (local):", time.asctime(time.localtime(now)), time.strftime("%Z"))
    #print("Time since reset asserted:", now - reset_start)
    time.sleep(0.1)	# ensure a reasonable reset time
    #print("Time since reset asserted:", now - reset_start)

    # Turn the chip on.
    tdc.on()

    # Make sure our internal copy of the register state is up to date.
    #print("Reading chip side #1 register state:")
    tdc.read_regs1()
    #tdc.print_regs1()

    # Measure average time per measurement.
    iters = 1000
    then = time.time()
    resultList = [0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    for m in range(iters):
        resultList[tdc.measure(simulate=True)] += 1
        tdc.clear_status() # Clear interrupt register bits to prepare for next measurement
    print(resultList)
    now = time.time()
    duration = now - then
    print(iters,"measurements in",duration,"seconds")
    print((iters/duration),"measurements per second")
    print((duration/iters),"seconds per measurement")
    # Read it back to make sure.
    #tdc.read_regs1()
    #tdc.print_regs1()

    # Turn the chip off.
    tdc.off()
