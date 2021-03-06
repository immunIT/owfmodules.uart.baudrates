# -*- coding: utf-8 -*-

# Octowire Framework
# Copyright (c) ImmunIT - Jordan Ovrè / Paul Duncan
# License: Apache 2.0
# Paul Duncan / Eresse <pduncan@immunit.ch>
# Jordan Ovrè / Ghecko <jovre@immunit.ch>

import codecs
import time

from octowire_framework.module.AModule import AModule
from octowire_framework.core.commands.miniterm import miniterm
from octowire.uart import UART
from octowire.gpio import GPIO
from octowire.utils.serial_utils import detect_octowire
from prompt_toolkit import prompt


class BaudrateAscii(AModule):
    def __init__(self, owf_config):
        super(BaudrateAscii, self).__init__(owf_config)
        self.meta.update({
            'name': 'UART baudrate detection (ASCII)',
            'version': '2.0.0',
            'description': 'Perform UART baudrate detection for ASCII-based communication',
            'author': 'Jordan Ovrè / Ghecko <jovre@immunit.ch>, Paul Duncan / Eresse <pduncan@immunit.ch>'
        })
        self.options = {
            "uart_interface": {"Value": "", "Required": True, "Type": "int",
                               "Description": "UART interface (0=UART0 or 1=UART1)", "Default": 0},
            "mode": {"Value": "", "Required": True, "Type": "text",
                     "Description": "Method used to perform baudrate detection - see advanced options for details.\nIn "
                                    "'incremental' mode, the baudrate starts at 'baudrate_min' and is incremented by "
                                    "'baudrate_inc' up to 'baudrate_max'.\nIn the 'list' mode, all values "
                                    "defined in 'baudrate_list' will be tested.\nAcceptable values: 'list' & "
                                    "'incremental'.", 
                     "Default": "incremental"},
            "reset_pin": {"Value": "", "Required": False, "Type": "int",
                          "Description": "GPIO used as slave reset. If defined, the module will pulse this GPIO to "
                                         "reset the target. See the 'reset_pol' advanced option to "
                                         "define the polarity.",
                          "Default": ""},
            "trigger": {"Value": "", "Required": True, "Type": "bool",
                        "Description": "When true, send the character(s) defined in 'trigger_char' (see advanced "
                                       "options) if the Octowire does not receive anything from the target.",
                        "Default": False}
        }
        self.advanced_options.update({
            "reset_pol": {"Value": "", "Required": True, "Type": "text",
                          "Description": "The polarity of the reset line to cause a reset on the target. "
                                         "Acceptable values: 'low' (active-low) & 'high'.",
                          "Default": "low"},
            "reset_hold": {"Value": "", "Required": True, "Type": "float",
                           "Description": "Hold time required to perform a target reset (in seconds).",
                           "Default": 0.1},
            "reset_delay": {"Value": "", "Required": True, "Type": "float",
                            "Description": "Time to wait after a target reset.",
                            "Default": 0.5},
            "baudrate_min": {"Value": "", "Required": True, "Type": "int",
                             "Description": "Minimum baudrate value. (Incremental mode only)",
                             "Default": 300},
            "baudrate_max": {"Value": "", "Required": True, "Type": "int",
                             "Description": "Maximum baudrate value. (Incremental mode only)",
                             "Default": 115200},
            "baudrate_inc": {"Value": "", "Required": True, "Type": "int",
                             "Description": "Baudrate increment value. (Incremental mode only)",
                             "Default": 300},
            "baudrate_list": {"Value": "", "Required": True, "Type": "text",
                              "Description": "Baudrate values to test (comma separated). (List mode only)",
                              "Default": "9600,19200,38400,57600,115200"},
            "trigger_char": {"Value": "", "Required": True, "Type": "hextobytes",
                             "Description": "Character(s) to send when the 'trigger' options is set to True. "
                                            "Format: raw hex (no leading '0x')",
                             "Default": "0D0A"},
        })
        self.baudrates = [9600, 19200, 38400, 57600, 115200]
        self.uart_instance = None
        self.reset_pin = None
        # Generate list of ascii characters including extended ones
        self.extended_asciitable = list(map(chr, range(0x20, 127)))
        self.extended_asciitable.append("\r")
        self.extended_asciitable.append("\n")
        self.extended_asciitable.append("\t")
        self.extended_asciitable.append(chr(0x1b))

    def check_options(self):
        """
        Check the user defined options.
        :return: Bool.
        """
        # If reset_pin is set and reset_pol invalid
        if self.options["reset_pin"]["Value"] != "":
            if self.advanced_options["reset_pol"]["Value"].upper() not in ["LOW", "HIGH"]:
                self.logger.handle("Invalid reset polarity.", self.logger.ERROR)
                return False
            if self.options["reset_pin"]["Value"] not in range(0, 15):
                self.logger.handle("Invalid reset pin.", self.logger.ERROR)
                return False
        # Check the mode
        if self.options["mode"]["Value"].upper() not in ["INCREMENTAL", "LIST"]:
            self.logger.handle("Invalid mode option. Please use 'incremental' or 'list'.", self.logger.ERROR)
            return False
        # Check the list if the selected mode is 'list'
        if self.options["mode"]["Value"].upper() == "LIST":
            try:
                baud_list = [b.strip() for b in self.advanced_options["baudrate_list"]["Value"].split(",")]
                if not baud_list:
                    self.logger.handle("Empty or invalid baudrate list.", self.logger.ERROR)
                    return False
            except:
                self.logger.handle("Invalid baudrate list", self.logger.ERROR)
        return True

    def wait_bytes(self):
        """
        Wait until receiving a byte (for 1 second) from the target.
        :return: Bool.
        """
        timeout = 1
        timeout_start = time.time()

        while time.time() < timeout_start + timeout:
            in_waiting = self.uart_instance.in_waiting()
            if in_waiting > 0:
                return True
        return False

    def change_baudrate(self, baudrate):
        """
        This function changes the baudrate for the target device.
        :param baudrate: Baudrate value
        :return: Bool.
        """
        self.logger.handle(f'Switching to baudrate {baudrate}...', self.logger.INFO)
        try:
            # Empty serial_instance buffer
            self.uart_instance.serial_instance.read(self.uart_instance.serial_instance.in_waiting)
            # Configure UART baudrate
            self.uart_instance.configure(baudrate=baudrate)
            # Empty UART in_waiting buffer
            self.uart_instance.receive(self.uart_instance.in_waiting())
            return True
        except (ValueError, Exception) as err:
            self.logger.handle(err, self.logger.ERROR)
            return False

    def trigger_device(self):
        """
        Send character(s) defined by the "trigger_char" advanced option.
        This method is called when no data was received during the baudrate detection.
        :return: Nothing.
        """
        self.logger.handle("Triggering the device", self.logger.INFO)
        self.uart_instance.transmit(self.advanced_options["trigger_char"]["Value"])
        time.sleep(0.2)

    def uart_pt_miniterm(self):
        """
        Open a miniterm session, with the Octowire in the UART passthrough mode
        if a valid baudrate value is found and the user selects 'yes' when asked.
        :return: Nothing.
        """
        self.uart_instance.passthrough()
        self.owf_serial.close()
        if self.config["OCTOWIRE"].getint("detect"):
            octowire_port = detect_octowire(verbose=False)
            self.config['OCTOWIRE']['port'] = octowire_port
        miniterm(None, self.config)
        self.logger.handle("Please press the Octowire User button to exit the UART "
                           "passthrough mode", self.logger.USER_INTERACT)

    def process_baudrate(self, baudrate):
        """
        Change the baudrate and check if bytes received on the RX pin are valid characters.
        20 valid characters are required to identify the correct baudrate value.
        :return: Bool.
        """
        count = 0
        loop = 0
        threshold = 20

        # Dynamic printing
        progress = self.logger.progress('Reading bytes')
        while True:
            if self.wait_bytes():
                tmp = self.uart_instance.receive(1)
                # Print character read dynamically
                try:
                    tmp.decode()
                    progress.status(tmp.decode())
                except UnicodeDecodeError:
                    tmp2 = tmp
                    progress.status('0x{}'.format(codecs.encode(tmp2, 'hex').decode()))
                # Try to decode the received byte
                try:
                    byte = tmp.decode('utf-8')
                except UnicodeDecodeError:
                    byte = tmp
                # Check if it is a valid character
                if byte in self.extended_asciitable:
                    count += 1
                else:
                    # Invalid character received, quit the loop and try with the next baudrate value
                    progress.stop()
                    self.logger.handle("{} does not appear to be a valid baudrate setting...".format(baudrate),
                                       self.logger.WARNING)
                    return False
                if count >= threshold:
                    progress.stop()
                    self.logger.handle("Valid baudrate found: {}".format(baudrate), self.logger.RESULT)
                    resp = prompt('Would you like to open a miniterm session or '
                                  'continue testing other baudrate values? N(o)/y(es)/c(ontinue): ')
                    if resp.upper() == 'Y':
                        self.uart_pt_miniterm()
                    # Continue testing other baudrate values
                    if resp.upper() == 'C':
                        return False
                    return True
            elif self.options["trigger"]["Value"] and loop < 3:
                loop += 1
                self.trigger_device()
                continue
            else:
                progress.stop()
                self.logger.handle("No data received using the following baudrate "
                                   "value: {}...".format(baudrate), self.logger.WARNING)
                return False

    def reset_target(self):
        """
        If the reset_pin option is set, reset the target.
        :return: Nothing
        """
        if self.reset_pin is not None:
            self.logger.handle("Attempting to reset the target..", self.logger.INFO)
            if self.advanced_options["reset_pol"]["Value"].upper() == "LOW":
                self.reset_pin.status = 0
                time.sleep(self.advanced_options["reset_hold"]["Value"])
                self.reset_pin.status = 1
            else:
                self.reset_pin.status = 1
                time.sleep(self.advanced_options["reset_hold"]["Value"])
                self.reset_pin.status = 0
            time.sleep(self.advanced_options["reset_delay"]["Value"])

    def init(self):
        """
        Configure the UART and the reset interface (if defined).
        :return:
        """
        # Set and configure UART interface
        self.uart_instance = UART(serial_instance=self.owf_serial, interface_id=self.options["uart_interface"]["Value"])

        # Ensure reset_pin is set to None before initialized it if needed
        self.reset_pin = None
        # Configure the reset line if defined
        if self.options["reset_pin"]["Value"] != "":
            self.reset_pin = GPIO(serial_instance=self.owf_serial, gpio_pin=self.options["reset_pin"]["Value"])
            self.reset_pin.direction = GPIO.OUTPUT
            if self.advanced_options["reset_pol"]["Value"].upper() == "LOW":
                self.reset_pin.status = 1
            else:
                self.reset_pin.status = 0

    def incremental_mode(self):
        """
        Check for valid baudrates using the incremental mode.
        :return: Nothing.
        """
        for baudrate in range(self.advanced_options["baudrate_min"]["Value"],
                              self.advanced_options["baudrate_max"]["Value"],
                              self.advanced_options["baudrate_inc"]["Value"]):
            if self.change_baudrate(baudrate=baudrate):
                self.reset_target()
                if self.process_baudrate(baudrate=baudrate):
                    # Stop the loop if valid baudrate is found
                    break

    def list_mode(self):
        """
        Check for valid baudrates using the list mode.
        :return: Nothing.
        """
        for baudrate in [int(b.strip()) for b in self.advanced_options["baudrate_list"]["Value"].split(",")]:
            if self.change_baudrate(baudrate=baudrate):
                self.reset_target()
                if self.process_baudrate(baudrate=baudrate):
                    # Stop the loop if valid baudrate is found
                    break

    def run(self):
        """
        Main function.
        Try to detect a valid UART baudrate.
        :return: Nothing.
        """
        # If detect_octowire is True then detect and connect to the Octowire hardware. Else, connect to the Octowire
        # using the parameters that were configured. This sets the self.owf_serial variable if the hardware is found.
        self.connect()
        if not self.owf_serial:
            return
        try:
            if self.check_options():
                self.init()
                self.logger.handle("Starting baudrate detection, turn on your target device now", self.logger.HEADER)
                self.logger.handle("Press Ctrl+C to cancel", self.logger.HEADER)
                if self.options["mode"]["Value"].upper() == "INCREMENTAL":
                    self.incremental_mode()
                elif self.options["mode"]["Value"].upper() == "LIST":
                    self.list_mode()
            else:
                return
        except (Exception, ValueError) as err:
            self.logger.handle(err, self.logger.ERROR)
