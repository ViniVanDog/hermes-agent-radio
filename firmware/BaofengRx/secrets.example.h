#pragma once

// Copy this file to secrets.h (git-ignored) and fill it in.
// Both firmwares (classic ESP32 and ESP32-C3) include it.

// Wi-Fi networks, tried in order until one connects.
struct WifiNetwork {
  const char* ssid;
  const char* password;
};

constexpr WifiNetwork WIFI_NETWORKS[] = {
    {"your-ssid", "your-password"},
    // {"backup-ssid", "backup-password"},
};
constexpr size_t WIFI_NETWORK_COUNT = sizeof(WIFI_NETWORKS) / sizeof(WIFI_NETWORKS[0]);

// LAN address of the PC that receives RX audio (bridge.py / udp_relay.py).
// Never 127.0.0.1: on the ESP32 that is the ESP32 itself.
constexpr char HOST_PC_IP[] = "192.0.2.10";
constexpr uint16_t UDP_AUDIO_PORT = 7777;

// false = only print ADC statistics over USB until you type `u`.
constexpr bool START_AUDIO_STREAM = true;
constexpr uint32_t WIFI_CONNECT_TIMEOUT_PER_NETWORK_MS = 8000;
