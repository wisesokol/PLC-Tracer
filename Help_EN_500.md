# Complete Allen-Bradley SLC 500 instruction reference
# Every instruction with a short description in English
# Source: Publication 1747-RM001G-EN-P (SLC 500 Instruction Set Reference Manual)
# Processors: SLC 500, SLC 5/01, SLC 5/02, SLC 5/03, SLC 5/04, SLC 5/05

---

## BIT / RELAY INSTRUCTIONS

```
XIC  — Examine If Closed
       Checks whether the addressed bit is set to 1. If it is, the rung continues.
       The equivalent of a normally open contact in relay logic.
       Addressing: B3:0/0, I:1/0, O:0/0 and so on.

XIO  — Examine If Open
       Checks whether the addressed bit is cleared to 0. If it is, the rung continues.
       The equivalent of a normally closed contact in relay logic.

OTE  — Output Energize
       Sets the bit to 1 while the rung condition is true, clears it to 0 when false.
       The equivalent of a relay coil — its state mirrors the state of the rung.
       Do not use the same OTE address in more than one rung.

OTL  — Output Latch
       Sets the bit to 1 when the rung condition is true. Does NOT clear it when false.
       The bit stays at 1 until it is explicitly cleared by an OTU. A retentive output.

OTU  — Output Unlatch
       Clears the bit to 0 when the rung condition is true. Used together with OTL.
       OTL and OTU must address the same bit.

OSR  — One Shot Rising
       Sets the output bit to 1 for a single scan when the rung condition goes
       from false to true (rising edge). Uses a storage bit to track the previous
       state. Available from SLC 5/02 onwards.
```

---

## TIMERS

```
TON  — Timer On Delay
       Starts timing while the rung condition is true. When ACC >= PRE the DN bit
       is set. When the condition goes false the ACC is reset to 0.
       Address: T4:n. Elements: .PRE (preset), .ACC (accumulator).
       Bits: .EN (enabled), .TT (timing), .DN (done).
       Time base: 1.0 s or 0.01 s.

TOF  — Timer Off Delay
       Starts timing when the rung condition goes from true to false.
       The DN bit is set while the condition is true and stays set for the
       delay time after the condition drops out.
       While the condition is false it times out, then clears DN.

RTO  — Retentive Timer On
       Like TON, but the accumulator is NOT reset when the condition goes false.
       The ACC value is retained between scans and can accumulate time across
       several periods of being true. Reset only through a RES instruction.
```

---

## COUNTERS

```
CTU  — Count Up
       Increments ACC by 1 on every false-to-true transition of the rung.
       Sets DN when ACC >= PRE. Sets OV when ACC goes above +32767.
       Address: C5:n. Elements: .PRE, .ACC. Bits: .CU, .DN, .OV, .UN.

CTD  — Count Down
       Decrements ACC by 1 on every false-to-true transition of the rung.
       Sets DN when ACC >= PRE. Sets UN when ACC goes below -32768.

HSC  — High-Speed Counter
       A hardware counter running asynchronously to the program scan.
       Counts external pulses at rates up to several kHz.
       Fixed SLC 500 controllers only. Address: C5:0.
       Requires jumper J2 on the controller board to be cut.

RES  — Reset
       Resets ACC to 0 and clears every status bit (EN, TT, DN, OV, UN) for
       timers (T4:), counters (C5:) and control structures (R6:).
```

---

## COMPARISON INSTRUCTIONS

```
EQU  — Equal
       True when source_A = source_B.
       Types: integer (N7:), float (F8:).

NEQ  — Not Equal
       True when source_A ≠ source_B.

LES  — Less Than
       True when source_A < source_B.

LEQ  — Less Than or Equal
       True when source_A <= source_B.

GRT  — Greater Than
       True when source_A > source_B.

GEQ  — Greater Than or Equal
       True when source_A >= source_B.

MEQ  — Masked Comparison for Equal
       Compares source and compare through a bit mask.
       True when the bits selected by the mask match.
       The mask can be a constant or a word address.

LIM  — Limit Test
       Tests whether a value falls inside a given range.
       True when low_limit <= test <= high_limit.
       When low_limit > high_limit the test is inverted.
```

---

## MATH INSTRUCTIONS

```
ADD  — Add
       dest = source_A + source_B. Updates the S:0 bits (carry, overflow, zero, sign).

SUB  — Subtract
       dest = source_A - source_B.

MUL  — Multiply
       dest = source_A × source_B. The full 32-bit result goes to the math register S:13-S:14.

DIV  — Divide
       dest = source_A / source_B. The remainder is held in the math register S:14.
       Division by 0 causes a minor fault (error 0020h).

DDV  — Double Divide
       Divides the 32-bit value in the math register (S:13-S:14) by source.
       Used after MUL for extended precision.

CLR  — Clear
       Sets dest to 0. Works with integer and float types.

SQR  — Square Root
       dest = √source. For integers the fractional part is truncated.

NEG  — Negate
       dest = -source. Reverses the sign of the number.

ABS  — Absolute Value
       dest = |source|. Available on SLC 5/03 and above.

CPT  — Compute
       Evaluates an arbitrary mathematical expression.
       Supports: +, -, *, /, **, SIN, COS, TAN, ASN, ACS, ATN,
       LN, LOG, SQR, ABS, FRD, TOD, DEG, RAD, TRN, NEG.
       Available on SLC 5/03 and above.

SCP  — Scale with Parameters
       Linear scaling: dest = (input - input_min) ×
       (scaled_max - scaled_min) / (input_max - input_min) + scaled_min.
       Convenient for converting analog signals. SLC 5/03+.

SCL  — Scale Data
       dest = (source × rate / 10000) + offset.
       The rate is entered as 1/10000 of the multiplier.
       Available on every SLC 500.

RMP  — Ramp
       Drives the output linearly from its current value towards a target
       value at a given rate. Used for smooth transitions.
       SLC 5/03 and above.

SWP  — Swap
       Swaps the high and low bytes of every word in the given array.
       Used for endian conversion.
       SLC 5/03 and above.

XPY  — X to the Power of Y
       dest = source_A ^ source_B. SLC 5/03+.
```

---

## TRIGONOMETRIC INSTRUCTIONS (SLC 5/03 and above)

```
SIN  — Sine
       dest = sin(source). The argument is in radians. The result is REAL (F8:).

COS  — Cosine
       dest = cos(source). The argument is in radians.

TAN  — Tangent
       dest = tan(source). The argument is in radians.

ASN  — Arc Sine
       dest = arcsin(source). The result is in radians. Input: -1.0 ... 1.0.

ACS  — Arc Cosine
       dest = arccos(source). The result is in radians. Input: -1.0 ... 1.0.

ATN  — Arc Tangent
       dest = arctan(source). The result is in radians.

LN   — Natural Log
       dest = ln(source). Input > 0.

LOG  — Log to Base 10
       dest = log10(source). Input > 0.
```

---

## DATA HANDLING INSTRUCTIONS

```
TOD  — Convert to BCD
       Converts an integer (0..9999999) into binary coded decimal.
       The full result goes to the math register S:13-S:14 (32-bit BCD).

FRD  — Convert from BCD
       Converts a BCD value back into an integer.
       The full result goes to the math register S:13-S:14.

DEG  — Radians to Degrees
       dest = source × (180/π). SLC 5/03+.

RAD  — Degrees to Radians
       dest = source × (π/180). SLC 5/03+.

DCD  — Decode 4 to 1 of 16
       Sets one bit in a 16-bit word based on a 4-bit input.
       With source = 5, bit 5 of the destination is set to 1 and the rest to 0.

ENC  — Encode 1 of 16 to 4
       The reverse of DCD: finds the lowest set bit in a 16-bit word and
       writes its number (0..15) to the destination.

MOV  — Move
       Copies source to destination. A single element (16-bit integer or float).

MVM  — Masked Move
       Copies source to destination through a mask. Bits not selected by the
       mask are left unchanged in the destination.

AND  — Bitwise AND
       dest = source_A AND source_B.

OR   — Bitwise OR
       dest = source_A OR source_B.

XOR  — Exclusive OR
       dest = source_A XOR source_B.

NOT  — Bitwise NOT
       dest = NOT source. Inverts all 16 bits.

COP  — Copy File
       Copies a block of length elements from source to destination.
       Works with any file type (N7:, T4:, F8: and others).
       Is NOT interrupted by communications — safe for data consistency.

FLL  — Fill File
       Fills length elements of the destination array with the source value.
```

---

## FIFO / LIFO (QUEUES AND STACKS)

```
FFL  — FIFO Load
       Appends the source value to the end of the FIFO buffer on a rising edge.
       The R6:n control structure tracks position and length.
       Bits: .EN, .DN (full), .EM (empty).

FFU  — FIFO Unload
       Removes the first element from the FIFO and places it in the destination.
       First in, first out.

LFL  — LIFO Load
       Pushes the source value onto the top of the LIFO stack.

LFU  — LIFO Unload
       Pops the top element of the LIFO into the destination.
       Last in, first out.
```

---

## SHIFT REGISTERS

```
BSL  — Bit Shift Left
       Shifts every bit of the array one position left on a rising edge.
       Loads source_bit into bit 0. The highest bit goes into UL (unload bit).
       Control structure R6:n; the Length parameter sets the size.

BSR  — Bit Shift Right
       Shifts every bit of the array one position right.
       Loads source_bit into the highest bit. The lowest bit goes into UL.
```

---

## SEQUENCERS

```
SQO  — Sequencer Output
       On every false-to-true transition it steps to the next position in the
       array and writes that value to the destination through a mask.
       Used for step-by-step output control (like a drum controller).
       Control structure R6:n. Bits: .EN, .DN (reached the end), .FD.

SQC  — Sequencer Compare
       Compares the current input data against the current array position
       through a mask. Sets the FD (found) bit when the data matches.

SQL  — Sequencer Load
       Loads the source value into the current position of the sequencer array.
       Steps the position on every transition of the condition.
```

---

## PROGRAM CONTROL

```
JMP  — Jump to Label
       Jumps to the given LBL, skipping every rung in between.
       Jumping forward means the skipped rungs are not executed.
       Jumping backward re-executes rungs (careful: watchdog).

LBL  — Label
       Marks the target of a JMP. Occupies the first position in the rung.
       The label is an integer, 0..999.

JSR  — Jump to Subroutine
       Calls the given subroutine file (File 3..255).
       SLC 5/03+: passes up to 3 input and 3 output parameters.
       Nesting: up to 8 levels (SLC 5/02), up to 16 levels (SLC 5/03+).

SBR  — Subroutine
       Placed in the first rung of the called routine. Receives the input parameters.
       SLC 5/03+: up to 3 input parameters.

RET  — Return
       Ends the subroutine and returns control to the calling routine.
       SLC 5/03+: up to 3 return parameters.

MCR  — Master Control Reset
       Used in pairs: the first MCR opens the zone, the second closes it.
       While the rung holding the first MCR is false, every non-retentive output
       inside the zone is cleared. Retentive outputs (OTL) are not affected.

TND  — Temporary End
       Ends the current scan of the routine. The next scan starts from the top.
       In the main program it moves execution on to the I/O update.

SUS  — Suspend
       Puts the processor into Suspend (Idle) mode. Writes an ID number into
       status word S:7 for diagnostics. Used for troubleshooting.

IIM  — Immediate Input with Mask
       Reads input data from the given slot immediately, without waiting for the
       end of the scan. The mask selects which bits are updated. SLC 5/02+.

IOM  — Immediate Output with Mask
       Writes output data to the given slot immediately, without waiting for the
       end of the scan. The mask selects which bits are updated. SLC 5/02+.

REF  — I/O Refresh
       Updates every input and output immediately (a full refresh).
       SLC 5/02: updates all I/O. SLC 5/03+: updates I/O and services communications.
```

---

## SPECIALTY INSTRUCTIONS

```
RHC  — Read High-Speed Clock
       Reads the processor's internal high-speed clock (1 µs resolution)
       into the given address. SLC 5/03+.
       Used for precise measurement of time intervals.

TDF  — Compute Time Difference
       Computes the difference between two time stamps taken with RHC.
       The result is an interval in microseconds. SLC 5/03+.

FBC  — File Bit Comparison
       Compares two arrays bit by bit and finds the first bit that differs.
       Modes: All (all in one scan), Incremental (one per scan).

DDT  — Diagnostic Detect
       Compares two arrays bit by bit through a reference mask.
       Finds the differences (diagnostics) and writes them to the result array.

RPC  — Read Program Checksum
       Reads the checksum of the given program file.
       Used to detect changes in the logic. SLC 5/03+.
```

---

## BLOCK TRANSFERS (REMOTE I/O)

```
BTR  — Block Transfer Read
       Reads up to 64 words of data from a remote device over Remote I/O.
       Control structure: 3 integer words (N7: file).
       File address: M1. An asynchronous operation — it does not block the scan.

BTW  — Block Transfer Write
       Writes up to 64 words of data to a remote device over Remote I/O.
       File address: M0. Control structure: 3 integer words.
```

---

## PID CONTROL

```
PID  — Proportional Integral Derivative
       A PID controller for closed-loop control.
       Control block: 23 words in an integer file (N7: or PD).
       Parameters: Kc (gain), Ti (integral time), Td (derivative time),
       SP (setpoint), PV (process variable), CV (control output),
       feed forward/bias, deadband.
       Modes: Auto/Manual, Timed/STI mode, E (error)/PV (derivative action).
       Setpoint scaling: SMAX, SMIN. Output limiting: CVH, CVL.
       The update period is set in the controller parameters or by the STI.
```

---

## ASCII INSTRUCTIONS (SLC 5/03 and above)

```
ABL  — ASCII Test Buffer for Line
       Tests whether a complete line (with its terminator) is present in the
       serial port input buffer. Sets FD when a line is found.

ACB  — ASCII Chars in Buffer
       Returns the number of characters in the channel input buffer.
       The result goes to the .POS element of the control structure.

ACI  — ASCII String to Integer
       Converts an ASCII string (for example "1234") into an integer.
       Input: a string file (ST). Output: integer.

AIC  — Integer to ASCII String
       Converts an integer into an ASCII string.
       Input: integer. Output: a string file (ST).

ACL  — ASCII Clear Buffer
       Clears the input and/or output buffer of the serial port.
       Parameters select which buffer is cleared.

ACN  — String Concatenate
       Joins two ASCII strings. The result goes to the destination.

AEX  — String Extract
       Extracts a substring from a string at a given position and length.

AHL  — ASCII Handshake Lines
       Reads and sets the modem control lines (DTR, RTS, CTS, DSR)
       through AND and OR masks.

ARD  — ASCII Read Characters
       Reads a given number of characters from the port input buffer.
       An asynchronous operation. The result goes to a string file.

ARL  — ASCII Read Line
       Reads characters from the buffer up to the terminator (end of line).
       The terminator is set by the channel configuration.

ASC  — String Search
       Searches for a substring inside a string. Returns the position of the
       first occurrence.

ASR  — ASCII String Compare
       Compares two ASCII strings. Sets FD when they match.

AWA  — ASCII Write Append
       Writes a string to the port and appends the termination characters
       (CR, LF and so on) from the channel configuration.

AWT  — ASCII Write
       Writes a string to the port without appending termination characters.
```

---

## COMMUNICATION INSTRUCTIONS

```
MSG  — Message
       Sends and receives data over DH-485, DH+, Ethernet or DF1.
       Supports reads and writes to SLC, PLC-5, MicroLogix, Logix and others.
       Control structure: an integer file (50+ words for SLC 5/03+).
       Asynchronous: it does not block the scan. Bits: .EN, .DN, .ER, .ST.

SVC  — Service Communications
       Forces the communication channel (DH-485, DH+) to be serviced.
       Used to speed up MSG handling in long programs.

CEM  — ControlNet Explicit Message
       Sends an explicit CIP message over ControlNet through a 1747-SCNR scanner.
       SLC 5/03 and SLC 5/04.

DEM  — DeviceNet Explicit Message
       Sends an explicit message over DeviceNet through a 1747-SDN scanner.

EEM  — EtherNet/IP Explicit Message
       Sends an explicit CIP message over Ethernet. SLC 5/05 only.
```

---

## INTERRUPT INSTRUCTIONS

```
STD  — Selectable Timed Disable
       Disables the STI (Selectable Timed Interrupt) for the duration of a
       critical section of code. Used together with STE.

STE  — Selectable Timed Enable
       Re-enables the STI. The counterpart of STD.

STS  — Selectable Timed Start
       Changes the STI parameters from the program: the subroutine file
       and/or the interrupt interval. SLC 5/03+.

IID  — I/O Interrupt Disable
       Disables interrupts from I/O modules. SLC 5/03+.

IIE  — I/O Interrupt Enable
       Re-enables interrupts from I/O modules. SLC 5/03+.

RPI  — Reset Pending Interrupt
       Clears a pending I/O interrupt that occurred while interrupts
       were disabled. SLC 5/03+.

INT  — Interrupt Subroutine
       Marks a subroutine as an I/O interrupt handler.
       Placed in the first rung of the handler subroutine.
```

---

## INSTRUCTION AVAILABILITY BY PROCESSOR

```
Instruction    | SLC 500 Fixed | 5/01 | 5/02 | 5/03 | 5/04 | 5/05
---------------|:------------:|:----:|:----:|:----:|:----:|:----:
XIC,XIO,OTE    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
OTL,OTU        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
OSR            |      —       |  —   |  ✓   |  ✓   |  ✓   |  ✓
TON,TOF,RTO    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
CTU,CTD,RES    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
HSC            |      ✓       |  —   |  —   |  —   |  —   |  —
EQU..GEQ       |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
MEQ,LIM        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
ADD,SUB,MUL,DIV|      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
DDV            |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
CLR,SQR,NEG    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
SCL            |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
CPT,SCP,ABS    |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
SWP,XPY,RMP    |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
SIN..ATN       |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
LN,LOG,DEG,RAD |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
TOD,FRD        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
DCD,ENC        |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
MOV,MVM        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
AND,OR,XOR,NOT |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
COP,FLL        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
FFL,FFU        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
LFL,LFU        |      —       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
BSL,BSR        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
SQO,SQC,SQL    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
JMP,LBL        |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
JSR,SBR,RET    |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
MCR            |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
TND            |      ✓       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
SUS            |      —       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
IIM,IOM        |      —       |  —   |  ✓   |  ✓   |  ✓   |  ✓
REF            |      —       |  —   |  ✓   |  ✓   |  ✓   |  ✓
FBC,DDT        |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
RHC,TDF        |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
RPC            |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
BTR,BTW        |      —       |  —   |  ✓   |  ✓   |  ✓   |  —
PID            |      —       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
ASCII(ABL..AWT)|      —       |  —   |  —   |  ✓   |  ✓   |  ✓
MSG            |      —       |  ✓   |  ✓   |  ✓   |  ✓   |  ✓
SVC            |      —       |  —   |  ✓   |  ✓   |  ✓   |  ✓
STD,STE,STS    |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
IID,IIE,RPI    |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
INT            |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
CEM            |      —       |  —   |  —   |  ✓   |  ✓   |  —
DEM            |      —       |  —   |  —   |  ✓   |  ✓   |  ✓
EEM            |      —       |  —   |  —   |  —   |  —   |  ✓
```

---

## SLC 500 DATA FILE STRUCTURE

```
File   | Type       | Description
-------|------------|--------------------------------------------------
O0:    | Output     | Output data (mapped to the output modules)
I1:    | Input      | Input data (mapped to the input modules)
S2:    | Status     | Processor status file (faults, modes, clock)
B3:    | Bit        | Bit file (internal flags and relays)
T4:    | Timer      | Timer file (structure: PRE, ACC, EN, TT, DN)
C5:    | Counter    | Counter file (structure: PRE, ACC, CU, CD, DN, OV, UN)
R6:    | Control    | Control file (BSL, BSR, FFL, FFU, SQO, SQC, SQL)
N7:    | Integer    | Integer file (16-bit signed: -32768..32767)
F8:    | Float      | Floating point file (32-bit IEEE float)
9-255  | User       | User files (any type: N, F, B, T, C, R, ST and others)
ST     | String     | String file (82 characters max) — SLC 5/03+
```

---

## SLC 500 ADDRESSING

```
Format: [File Type][File Number]:[Element][.Field][/Bit]

Examples:
  O:0/0         — output slot 0, bit 0
  I:1/0         — input slot 1, bit 0
  B3:0/0        — bit file 3, word 0, bit 0
  T4:0.ACC      — timer 0, accumulator
  T4:0.PRE      — timer 0, preset
  T4:0/DN       — timer 0, Done bit
  C5:0.ACC      — counter 0, accumulator
  N7:0          — integer file 7, word 0
  F8:0          — float file 8, element 0
  R6:0.LEN      — control file, length
  R6:0.POS      — control file, position
  S:1/15        — status, word 1, bit 15 (first pass bit)

Indexed addressing (SLC 5/02+):
  N7:[N7:0]     — the word address is given by the value of N7:0

Indirect addressing (SLC 5/03+):
  N[N7:0]:0     — the file number is given by the value of N7:0
```
