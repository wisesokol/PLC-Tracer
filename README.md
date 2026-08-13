# PLC Tracer

[English](#english) | [Русский](#russian)

<a name="english"></a>
<p align="center">
  <img src="Logo\Logo.png" alt="Logo" width="200">
</p>
## English

Offline & Online ladder logic visualizer for Allen-Bradley SLC 500 / MicroLogix / Logix 5000.

## Video

- [Intro Presentation](https://youtu.be/5EIQuA3fAio)
- [Video about Functionality](https://youtu.be/EdFXSBzqMIg)
- [New Mode HMI (SCADA)](https://youtu.be/KupVDB3ziWQ?si=2AiPk1xtRZD8GsSl)

PLC-Tracer is a free, browser-based diagnostic tool for Allen-Bradley PLCs 
(ControlLogix, CompactLogix, MicroLogix, and PLC-5). It parses and visualizes 
ladder logic using both offline and online data. The tool can record live 
variable states as a graph via a direct PLC connection and play back sessions 
offline.The goal of the project is not to replace Allen-Bradley software, 
but to make industrial troubleshooting more affordable and intuitive. 
By lowering the financial barrier, PLC-Tracer empowers maintenance engineers 
and technicians—especially in developing countries—to diagnose equipment 
efficiently without the burden of $3,000–$10,000 licensing costs. Built entirely with Claude.

Important:
For safety reasons, PLC-Tracer is strictly read-only — it monitors and 
visualizes PLC data but cannot write values or modify the controller logic in any way.

### Problem and solution

#### Problem
- Manual cross-referencing: engineers must manually correlate hundreds of addresses across fragmented files.
- High operational risk: program fragmentation delays fault detection, increasing human error and equipment downtime.

#### Solution
- Click any tag → see every rung that reads or writes it, connected by wires.
- Load live PLC values via WebSocket → rungs light up green/red in real time.
- Record a session → replay it frame by frame in the Player.

## Core module — Ladder Visualizer
- `plc-tracer-500-v160.html` — main visualizer.
- Loads `.SLC` (RSLogix 500) and `.L5X` (Studio 5000) exports.
- Tag trace: click a tag and build a graph of linked rungs.
- Full routine view: show all rungs at once.
- Live mode: WebSocket updates rungs in real time.
- REC records sessions to `.ndrec` files.
- Built-in instruction reference for SLC 500 and Logix 5000.
- `.PRN` snapshots for offline data table analysis.
- Back/Forward history and Bézier wire traces.

## Bridge module — Live Data Acquisition
- `Bridge/plc_bridge_500.py` — headless bridge.
- `Bridge/plc_bridge_ui.py` — `tkinter` GUI bridge.
- SLC 5/05, MicroLogix 1100–1500 via PCCC (`libplctag`).
- ControlLogix / CompactLogix via EtherNet/IP (`libplctag` in the current implementation).
- Dark GUI: start / stop / reconnect, color-coded log.
- Streams tag values over WebSocket to browser on port `8765`.

## Player module — Session Replay
- `PLC_Tracer_Player.html` — replay `.ndrec` recordings.
- Timeline: colored ON/OFF segments per tag channel.
- Playback: Play/Pause, speed ×0.5–×8, frame step left/right.
- Zoom in/out on timeline and hover tooltips.
- Keyboard: `Space` = play, right-click for channel management.

## Use cases

### Offline Debug
- Load an exported program.
- Trace tag dependencies across the full routine.
- Review `.PRN` snapshots without a PLC connection.

### Live Monitoring
- Connect through the Bridge.
- Watch rungs light up green and red in real time.
- Record sessions with REC for later review.

### Post-Mortem Replay
- Open a `.ndrec` file in the Player.
- Step through an incident frame by frame.
- Correlate rung states with the timeline.

## Repository structure

- `plc-tracer-500-v160.html` — main PLC Tracer visualizer.
- `PLC_Tracer_Player.html` — recording player.
- `Bridge/plc_bridge_500.py` — WebSocket Bridge.
- `Bridge/plc_bridge_ui.py` — GUI bridge.
- `Bridge/libplctag_2.6.16_windows_x64/plctag.dll` — native `libplctag` library.
- `Bridge/install.txt` — Bridge installation instructions.
- `requirements.txt` — Python dependencies.
- `LICENSE` — MIT license.

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`

## Running the Bridge

1. Install Python 3.10+ and add it to PATH.
2. Copy the `Bridge/` folder to the target machine.
3. Run:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the GUI Bridge:
   ```bash
   python Bridge/plc_bridge_ui.py
   ```
5. Or start the headless Bridge:
   ```bash
   python Bridge/plc_bridge_500.py --ip 192.168.1.10 --type slc
   python Bridge/plc_bridge_500.py --ip 192.168.1.10 --type logix
   ```

Without arguments, `Bridge/plc_bridge_500.py` starts interactive setup.

## External library `libplctag` https://github.com/libplctag/libplctag

The Bridge uses the external native `libplctag` library for live mode:

- `Bridge/libplctag_2.6.16_windows_x64/plctag.dll`

This file is not part of the source code and may have separate licensing terms.

## License

This repository source code is released under the MIT License. See `LICENSE`.

<a name="russian"></a>

## Русский

Offline & Online визуализатор лестничной логики для Allen-Bradley SLC 500 / MicroLogix / Logix 5000.

## Видео

- [Intro Presentation](https://youtu.be/5EIQuA3fAio)
- [Video about Functionality](https://youtu.be/EdFXSBzqMIg)
- [Новый режим - HMI (SCADA)](https://youtu.be/KupVDB3ziWQ?si=2AiPk1xtRZD8GsSl)

### Проблема и решение

#### Проблема
- Ручная перекрёстная проверка: инженерам приходится вручную сопоставлять сотни адресов в разрозненных файлах.
- Высокий операционный риск: фрагментация программы задерживает обнаружение ошибок, увеличивая человеческий фактор и простои оборудования.

#### Решение
- Клик по любому тегу → видны все ряды, где он читается или записывается, связанные проводами.
- Загрузка живых значений PLC через WebSocket → ряды загораются зелёным/красным в реальном времени.
- Запись сессии → воспроизведение покадрово в Player.

## Core module — Ladder Visualizer
- `plc-tracer-500-v160.html` — главный визуализатор PLC Tracer.
- Загрузка `.SLC` (RSLogix 500) и `.L5X` (Studio 5000) экспортов.
- Tag trace: клик по тегу строит граф связанных рун.
- Полный просмотр рутин: все ряды одновременно.
- Live mode: WebSocket → реальное время, ряды окрашиваются в зелёный/красный.
- REC записывает сессию в файл `.ndrec`.
- Встроенная справка по инструкциям SLC 500 и Logix 5000.
- `.PRN` snapshot для оффлайн-анализа таблиц данных.
- История назад/вперёд, Bézier-провода.

## Bridge module — Live Data Acquisition
- `Bridge/plc_bridge_500.py` — headless мост.
- `Bridge/plc_bridge_ui.py` — GUI-мост на `tkinter`.
- SLC 5/05, MicroLogix 1100–1500 через PCCC (`libplctag`).
- ControlLogix / CompactLogix через EtherNet/IP (`libplctag` в текущей реализации).
- Тёмный интерфейс: старт / стоп / переподключение, цветной лог.
- Поток теговых значений через WebSocket в браузер на порту `8765`.

## Player module — Session Replay
- `PLC_Tracer_Player.html` — проигрыватель записей `.ndrec`.
- Таймлайн: цветные ON/OFF сегменты по каналам тегов.
- Воспроизведение: Play/Pause, скорость ×0.5–×8, пофреймная прокрутка.
- Масштабирование таймлайна, подсказки при наведении.
- Клавиатура: `Space` = play, правый клик для управления каналами.

## Сценарии использования

### Offline Debug
- Загрузите экспортированную программу.
- Проследите зависимости тегов по всему рутину.
- Просмотрите `.PRN` snapshots без подключения к PLC.

### Live Monitoring
- Подключитесь через Bridge.
- Смотрите, как ряды загораются зелёным/красным в реальном времени.
- Запишите сессию кнопкой REC для последующего анализа.

### Post-Mortem Replay
- Откройте `.ndrec` файл в Player.
- Шагайте по инциденту кадр за кадром.
- Сопоставляйте состояния рун с таймлайном.

## Структура репозитория

- `plc-tracer-500-v160.html` — основной визуализатор PLC Tracer.
- `PLC_Tracer_Player.html` — проигрыватель записей.
- `Bridge/plc_bridge_500.py` — WebSocket Bridge.
- `Bridge/plc_bridge_ui.py` — GUI-версия моста.
- `Bridge/libplctag_2.6.16_windows_x64/plctag.dll` — нативная библиотека `libplctag`.
- `Bridge/install.txt` — инструкции установки Bridge на Windows.
- `requirements.txt` — зависимости Python.
- `LICENSE` — лицензия MIT.

## Требования

- Python 3.10+
- `pip install -r requirements.txt`

## Запуск Bridge

1. Установите Python 3.10+ и добавьте его в PATH.
2. Скопируйте папку `Bridge/` на целевой компьютер.
3. Выполните:
   ```bash
   pip install -r requirements.txt
   ```
4. Запустите GUI:
   ```bash
   python Bridge/plc_bridge_ui.py
   ```
5. Или запустите консольную версию:
   ```bash
   python Bridge/plc_bridge_500.py --ip 192.168.1.10 --type slc
   python Bridge/plc_bridge_500.py --ip 192.168.1.10 --type logix
   ```

Без аргументов `Bridge/plc_bridge_500.py` предлагает интерактивную настройку.

## Внешняя библиотека `libplctag` https://github.com/libplctag/libplctag

Для онлайн-режима Bridge используется сторонняя нативная библиотека `libplctag`:

- `Bridge/libplctag_2.6.16_windows_x64/plctag.dll`

Этот файл не входит в собственный исходный код проекта и может иметь собственные лицензионные условия.

## Лицензия

Исходный код этого репозитория распространяется под лицензией MIT. Смотрите `LICENSE`.
