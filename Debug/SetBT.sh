#!/bin/bash

# Get the Bluetooth MAC address (from hci0)
MAC=$(hciconfig hci0 | grep "BD Address" | awk '{print $3}')

# Remove colons (:) for a clean suffix
MAC_CLEAN=${MAC//:/}

# Concatenate with prefix "GeoClient"
BLE_NAME="GeoClient_${MAC_CLEAN}"

# Print the result
echo "$BLE_NAME"
