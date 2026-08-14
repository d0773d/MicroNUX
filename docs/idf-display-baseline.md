# Linux-Free ESP-IDF Display Baseline

This profile isolates the Waveshare 10.1-inch JD9365 display from Linux,
SD/MMC, and the ESP32-C6 transport. ESP-IDF v6.0.1 owns the display for the
entire runtime.

## Reference contract

The implementation is pinned to the verified Kit C configuration:

- ESP32-P4 revision 1.3/ECO2;
- JD9365 at 800x1280 in RGB565;
- 80 MHz DPI clock;
- two MIPI-DSI lanes at 1500 Mbps per lane; and
- MIPI D-PHY supplied by LDO channel 3 at 2.5 V.

This agrees with the Waveshare `13_Displaycolorbar` example at commit
`028473b3bac120d38589e1c18f8ea90daccc090c`, the YamUI display path at commit
`de1b723b9fc237e63e96fc82ce2975aa8b1a2ad4`, the Waveshare JD9365 component
2.0.0, and the ESP-IDF v6.0.1 MIPI-DSI API.

## Expected visible sequence

1. The panel stays dark during its reset and initialization sequence.
2. The initial MicroNUX framebuffer appears briefly.
3. Hardware-generated vertical color bars appear for five seconds.
4. The display returns to a static `MICRONUX / COLOR BURN IN / RUNNING` page.
5. A small cyan square toggles once per second. The status page remains active
   indefinitely under the ESP-IDF DPI driver.

Linux is never inspected or loaded. The standalone component excludes the
Linux jump, DMA-PMS handoff, and SD/MMC handoff sources from the linked image.

## Build

From PowerShell:

```powershell
.\scripts\idf-display-baseline.ps1
```

The command is build-only by default. It uses pinned ESP-IDF v6.0.1 and writes
artifacts to `build/idf-display-baseline`.

Flashing requires both explicit switches:

```powershell
.\scripts\idf-display-baseline.ps1 -Flash -ConfirmExactKitC -Port COM14
```

Do not run a serial terminal on the selected port while flashing.

## Sources

- [Waveshare ESP32-P4-Module-DEV-KIT documentation](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT)
- [Waveshare ESP32-P4-Platform repository](https://github.com/waveshareteam/ESP32-P4-Platform)
- [ESP-IDF v6 ESP32-P4 MIPI-DSI documentation](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/api-reference/peripherals/lcd/dsi_lcd.html)
- [YamUI Waveshare display wrapper](https://github.com/d0773d/yamui-device/blob/de1b723b9fc237e63e96fc82ce2975aa8b1a2ad4/components/kc_touch_display/src/kc_touch_display_waveshare_p4.c)
