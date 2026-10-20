"""Experimental Tigard SPC/MPC and SPI programmer; offline-testable, no RAM blobs.

MPSSE -> TAP -> OnCE -> memory -> flash; SPI uses pyftdi's SPI API.
See README for supported profiles, hardware validation status and limitations.
"""
__version__ = "0.5.0"
