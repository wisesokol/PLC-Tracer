# Complete Logix 5000 instruction reference
# Every instruction with a short description in English

---

## BIT / RELAY INSTRUCTIONS

```
XIC  — Examine If Closed
       Checks whether the addressed bit is set to 1. If it is, the rung continues.
       The equivalent of a normally open contact in relay logic.

XIO  — Examine If Open
       Checks whether the addressed bit is cleared to 0. If it is, the rung continues.
       The equivalent of a normally closed contact in relay logic.

OTE  — Output Energize
       Sets the bit to 1 while the rung condition is true and clears it to 0 when false.
       The equivalent of a relay coil — its state mirrors the state of the rung.

OTL  — Output Latch
       Sets the bit to 1 when the rung condition is true. Does NOT clear it when false.
       The bit stays at 1 until it is cleared by an OTU instruction.

OTU  — Output Unlatch
       Clears the bit to 0 when the rung condition is true. Used together with OTL.

ONS  — One Shot
       Lets the instructions after it execute for a single scan when the rung
       condition goes from false to true. On later scans it blocks execution.

OSR  — One Shot Rising
       Sets the output bit to 1 for a single scan when the condition goes from 0 to 1.
       Uses a storage bit to track the previous state.

OSF  — One Shot Falling
       Sets the output bit to 1 for a single scan when the condition goes from 1 to 0.
```

---

## TIMERS AND COUNTERS

```
TON  — Timer On Delay
       Starts timing while the rung condition is true. When the accumulator reaches
       the preset (PRE) the DN bit is set. It resets when the condition goes false.

TOF  — Timer Off Delay
       Starts timing when the rung condition goes FALSE (having been true).
       The DN bit stays set for the delay time after the condition drops out.

RTO  — Retentive Timer On
       Like TON, but the accumulator is NOT reset when the condition goes false.
       The value is retained between scans. Reset only through a RES instruction.

TONR — Timer On with Reset (FBD/ST)
       The TON equivalent for function block and structured text.
       It has a built-in Reset input.

TOFR — Timer Off with Reset (FBD/ST)
       The TOF equivalent for FBD/ST, with a reset input.

RTOR — Retentive Timer On with Reset (FBD/ST)
       The RTO equivalent for FBD/ST, with a reset input.

CTU  — Count Up
       Increments the accumulator by 1 on every false-to-true transition.
       Sets the DN bit when the accumulator >= the preset.

CTD  — Count Down
       Decrements the accumulator by 1 on every false-to-true transition.
       Sets the DN bit when the accumulator >= the preset.

CTUD — Count Up/Down (FBD/ST)
       A combined counter with separate inputs for counting up and down.
       Function block and structured text only.

RES  — Reset
       Resets the accumulator of a timer or counter to 0 along with every status
       bit (EN, TT, DN, OV, UN). Applies to TIMER, COUNTER and CONTROL structures.
```

---

## COMPARISON INSTRUCTIONS

```
EQU  — Equal To
       Compares two values. True when source_A = source_B.

NEQ  — Not Equal To
       True when source_A ≠ source_B.

GRT  — Greater Than
       True when source_A > source_B.

GEQ  — Greater Than or Equal To
       True when source_A >= source_B.

LES  — Less Than
       True when source_A < source_B.

LEQ  — Less Than or Equal To
       True when source_A <= source_B.

LIM  — Limit Test
       Tests whether a value lies between a low and a high limit.
       True when low_limit <= test <= high_limit.

MEQ  — Masked Equal To
       Compares source and compare through a bit mask. True when the bits
       selected by the mask match.

CMP  — Compare
       Lets you write an arbitrary comparison as an expression.
       More flexible, but slower than EQU/NEQ/GRT and the rest.
```

---

## MATH INSTRUCTIONS

```
ADD  — Add
       destination = source_A + source_B.

SUB  — Subtract
       destination = source_A - source_B.

MUL  — Multiply
       destination = source_A * source_B.

DIV  — Divide
       destination = source_A / source_B. The remainder is dropped for integer types.

MOD  — Modulo
       destination = source_A MOD source_B.

NEG  — Negate
       destination = -source. Reverses the sign of the number.

ABS  — Absolute Value
       destination = |source|. Returns the absolute value.

SQR  — Square Root
       destination = √source.

CPT  — Compute
       Evaluates an arbitrary mathematical expression and writes the result to the
       destination. Supports +, -, *, /, **, SIN, COS, TAN, ABS, SQR and others.

XPY  — X to the Power of Y
       destination = source_A ^ source_B.

LN   — Natural Log
       destination = ln(source).

LOG  — Log Base 10
       destination = log10(source).

FRD  — Convert from BCD
       Converts a binary coded decimal (BCD) value into an integer.

TOD  — Convert to BCD
       Converts an integer into binary coded decimal (BCD).

TRN  — Truncate
       Drops the fractional part of a REAL and writes the whole part to the destination.

CLR  — Clear
       Sets the destination to 0. For structures it zeroes every field.
```

---

## TRIGONOMETRIC INSTRUCTIONS

```
SIN  — Sine
       destination = sin(source). The argument is in radians.

COS  — Cosine
       destination = cos(source). The argument is in radians.

TAN  — Tangent
       destination = tan(source). The argument is in radians.

ASN  — Arc Sine
       destination = arcsin(source). The result is in radians.

ACS  — Arc Cosine
       destination = arccos(source). The result is in radians.

ATN  — Arc Tangent
       destination = arctan(source). The result is in radians.

DEG  — Radians to Degrees
       destination = source × (180/π).

RAD  — Degrees to Radians
       destination = source × (π/180).
```

---

## MOVE AND LOGICAL INSTRUCTIONS

```
MOV  — Move
       Copies source to destination. Works with any atomic type.

MVM  — Masked Move
       Copies from source to destination only the bits set in the mask.
       Bits not selected by the mask are left unchanged in the destination.

BTD  — Bit Field Distribute
       Copies a group of bits from the source (starting at source_bit) into the
       destination (starting at destination_bit), length bits long.

SWPB — Swap Byte
       Reverses the byte order of a value. Used for endian conversion when
       exchanging data with devices that use the opposite byte order.

AND  — Bitwise AND
       destination = source_A AND source_B. Sets a bit only where both
       operands hold a 1.

OR   — Bitwise OR
       destination = source_A OR source_B. Sets a bit where at least one
       operand holds a 1.

XOR  — Bitwise Exclusive OR
       destination = source_A XOR source_B. Sets a bit only where the operand
       bits differ.

NOT  — Bitwise NOT
       destination = NOT source. Inverts every bit.
```

---

## ARRAY / FILE INSTRUCTIONS

```
COP  — Copy
       Copies a block of length elements from source to destination.
       Is NOT interrupted by communication tasks.

CPS  — Synchronous Copy
       The same as COP, but performed atomically. It guarantees the data cannot be
       changed by another task while the copy is running.

FLL  — File Fill
       Fills length elements of the destination array with the source value.

AVE  — File Average
       Computes the arithmetic mean of the array elements into the destination.

SRT  — File Sort
       Sorts the array elements into ascending order.

STD  — File Standard Deviation
       Computes the standard deviation of the array elements.

FAL  — File Arithmetic and Logic
       Evaluates the given expression over every element of the array.
       Modes: All (all in one scan), Incremental (one per scan).

FSC  — File Search and Compare
       Searches the array for an element that satisfies the given expression.

SIZE — Size In Elements
       Returns the number of elements in the given dimension of an array.

DDT  — Diagnostic Detect
       Compares two arrays bit by bit and writes the differences to a result array.
       Used for diagnostics — finding the bits that changed.

FBC  — File Bit Compare
       Compares two arrays through a mask and finds the first match.
```

---

## FIFO / LIFO (QUEUES AND STACKS)

```
FFL  — FIFO Load
       Appends the source value to the end of the FIFO buffer. Works on the
       first in, first out principle.

FFU  — FIFO Unload
       Removes the first element of the FIFO buffer into the destination.

LFL  — LIFO Load
       Pushes the source value onto the top of the LIFO buffer (stack).

LFU  — LIFO Unload
       Pops the top element of the LIFO buffer (stack) into the destination.
       Works on the last in, first out principle.
```

---

## SHIFT REGISTERS

```
BSL  — Bit Shift Left
       Shifts every bit of the array one position left. The source_bit value is
       loaded into the lowest bit. The highest bit is unloaded into the UL bit.

BSR  — Bit Shift Right
       Shifts every bit of the array one position right. The source_bit value is
       loaded into the highest bit. The lowest bit is unloaded into the UL bit.
```

---

## SEQUENCERS

```
SQI  — Sequencer Input
       Compares the input data against the sequencer array through a mask.
       True when the data matches the current position.

SQO  — Sequencer Output
       Writes the value at the current sequencer array position to the destination
       through a mask. Used for step-by-step output control.

SQL  — Sequencer Load
       Loads the source value into the current position of the sequencer array.
```

---

## PROGRAM CONTROL

```
JSR  — Jump to Subroutine
       Calls the given subroutine (routine), passing input parameters and
       receiving return values.

SBR  — Subroutine
       Placed at the start of the called routine. Receives the input parameters from JSR.

RET  — Return
       Ends the subroutine and returns values to the calling routine.

JMP  — Jump to Label
       Jumps to the given LBL, skipping every rung between it and the current one.

LBL  — Label
       Marks the target of a JMP instruction.

JXR  — Jump to External Routine
       Calls an external routine (not written in Logix), passing parameters to it.

FOR  — For Loop
       Calls the given routine repeatedly, stepping an index variable from an
       initial to a final value by a given increment.

BRK  — Break
       Breaks out of a FOR loop and returns control to the calling routine.

TND  — Temporary End
       Ends the current scan of the routine. The next scan starts from the top.

MCR  — Master Control Reset
       Used in pairs: the first MCR opens the zone, the second closes it.
       While the rung holding the first MCR is false, every output in the zone is cleared.

AFI  — Always False Instruction
       Always returns a false condition. Used to disable a rung without deleting it.

NOP  — No Operation
       Does nothing. Used as a placeholder or to line up logic.

UID  — User Interrupt Disable
       Prevents the current task from being interrupted by higher-priority tasks.
       Makes critical sections of code atomic.

UIE  — User Interrupt Enable
       Allows the task to be interrupted again. Used together with UID.

EVENT — Trigger Event Task
       Triggers the given Event Task from the program.

EOT  — End of Transition
       Used in the rungs of an SFC transition routine. Sets the bit that decides
       the result of the transition (true/false).
```

---

## STRING INSTRUCTIONS

```
CONCAT — Concatenate Strings
         Joins string sourceA with string sourceB into the destination.

DELETE — Delete String Characters
         Deletes quantity characters from string source, starting at position start.

FIND   — Find String
         Searches for string search inside string source, starting at position start.
         Returns the position of the first occurrence in result.

INSERT — Insert String
         Inserts string sourceB into string sourceA at position start.

MID    — Middle String
         Extracts quantity characters from string source, starting at position start.

UPPER  — Convert to Upper Case
         Converts every character of string source to upper case.

LOWER  — Convert to Lower Case
         Converts every character of string source to lower case.

DTOS   — DINT to String
         Converts an integer (DINT) into its string representation.

STOD   — String to DINT
         Converts the string representation of a number back into a DINT.

RTOS   — REAL to String
         Converts a floating point number (REAL) into its string representation.

STOR   — String to REAL
         Converts the string representation of a number into a REAL.
```

---

## COMMUNICATIONS

```
MSG  — Message
       Sends data to and receives data from a remote device. Supports CIP, DH+,
       block transfers and reads/writes of PLC-2/3/5 and SLC data tables.
       Runs asynchronously — it does not block the scan.

GSV  — Get System Value
       Reads an attribute of a controller system object (Task, Module, Controller
       and others). For example: task execution time, serial number, module status.

SSV  — Set System Value
       Writes a value into an attribute of a controller system object.
       For example: change a task period, set a flag.

IOT  — Immediate Output
       Updates the output data of the given tag (module) immediately, without
       waiting for the end of the scan. Used for time-critical outputs.
```

---

## DIAGNOSTICS

```
DTR  — Data Transition
       Compares source against reference through a mask. When the bits differ the
       instruction is true and the reference is updated. Used to detect changes.
```

---

## PID CONTROL

```
PID  — Proportional Integral Derivative
       The classic PID controller for closed-loop control.
       Inputs: PV (process variable), setpoint, tieback, control output.
       Supports autotuning and manual/auto switching.

PIDE — Enhanced PID (FBD/ST)
       The improved PID for FBD and ST. Supports cascade control, modes
       (auto/manual/cascade/ratio), anti-windup, built-in level alarms
       (HH/H/L/LL) and rate-of-change alarms.
```

---

## ALARM INSTRUCTIONS

```
ALMA — Analog Alarm
       Monitors an analog input against the HH, H, L and LL levels and against
       rate of change (ROC+ / ROC-). Supports acknowledgement, suppression,
       disabling and shelving. Carries messages for the HMI.

ALMD — Digital Alarm
       Monitors a discrete (BOOL) input for an alarm condition.
       Supports acknowledgement, latching, suppression and disabling.

ALM  — Alarm (FBD)
       The alarm instruction for FBD. Monitors an analog input against the
       HH/H/L/LL levels and rate of change.
```

---

## SERIAL PORT (ASCII)

```
ABL  — ASCII Test Buffer for Line
       Tests whether a complete line (ending with the terminator character) is
       present in the serial port input buffer.

ACB  — ASCII Chars in Buffer
       Returns the number of characters held in the serial port input buffer.

ACL  — ASCII Clear Buffer
       Clears the input and/or output buffer of the serial port.

AHL  — ASCII Handshake Lines
       Reads and sets the serial port control lines (DTR, RTS) through
       AND and OR bit masks.

ARD  — ASCII Read
       Reads a given number of characters from the serial port input buffer.

ARL  — ASCII Read Line
       Reads characters from the buffer up to the terminator character (end of line).

AWA  — ASCII Write Append
       Writes a string to the serial port and appends the termination characters
       (carriage return, line feed and so on).

AWT  — ASCII Write
       Writes a string to the serial port without appending termination characters.
```

---

## PROCESS CONTROL INSTRUCTIONS (FBD/ST)

```
BAND — Boolean AND (FBD)
       Performs a logical AND over 4 inputs. Out = In1 AND In2 AND In3 AND In4.

BNOT — Boolean NOT (FBD)
       Inverts a single boolean input. Out = NOT In.

BOR  — Boolean OR (FBD)
       Performs a logical OR over 4 inputs. Out = In1 OR In2 OR In3 OR In4.

BXOR — Boolean Exclusive OR (FBD)
       Performs an XOR over two boolean inputs.

D2SD — Discrete 2-State Device
       Controls a two-state device (a valve open/closed, a motor on/off).
       Supports program, operator, manual and override modes.
       Monitors the feedback from the limit switches.

D3SD — Discrete 3-State Device
       Controls a three-state device (for example a valve: open/intermediate/closed).
       Three outputs, three feedback inputs, several control modes.

DEDT — Deadtime
       Delays the input signal by the given time. Used to compensate for
       transport delay in control loops.

DERV — Derivative
       Computes the derivative of the input signal with respect to time.
       Used in control loops for anticipatory correction.

DFF  — D Flip-Flop
       A digital D flip-flop: on a rising edge of Clock the output Q takes the D input.
       The Clear input resets the output. There is an inverted output QNot.

ESEL — Enhanced Select
       Selects one of 6 inputs according to the selector.
       Supports program, operator and override modes.

FGEN — Function Generator
       Piecewise linear approximation: builds an arbitrary function from arrays of
       X and Y points and interpolates between them.

HLL  — High/Low Limiter
       Limits the output value to the given high and low limits.
       Raises alarms when a limit is reached.

HPF  — High Pass Filter
       Passes the fast-changing components of a signal and suppresses the slow
       ones (the DC component). A first order digital filter.

INTG — Integrator
       Computes the integral of the input signal over time. Supports output
       limiting and initialisation.

JKFF — JK Flip-Flop
       A digital JK flip-flop with J, K, Clock and Clear inputs.
       The Clear input resets the output. There is an inverted output QNot.

LDL2 — Lead-Lag 2nd Order
       A second order lead-lag filter. Used to compensate for process dynamics
       in control loops.

LDLG — Lead-Lag
       A first order lead-lag filter. Corrects the phase response in control loops.

LPF  — Low Pass Filter
       Passes the slow-changing components of a signal and suppresses noise and
       high-frequency interference. A first order digital filter.

MAVE — Moving Average
       Computes the moving average of the input signal over a given number of
       samples. Smooths out noise.

MAXC — Maximum Capture
       Tracks and holds the maximum value of the input signal.
       Can be cleared by hand through the Reset input.

MINC — Minimum Capture
       Tracks and holds the minimum value of the input signal.

MSTD — Moving Standard Deviation
       Computes the standard deviation of the input signal over a moving window
       of the given size.

MUX  — Multiplexer
       Selects one of 8 inputs according to an integer selector.
       Out = In[Selector].

MVMT — Masked Move with Target
       Copies bits from source to target through a mask. Where the mask is 0 the
       value from target is kept (rather than the destination).

NTCH — Notch Filter
       Suppresses the signal at a particular frequency (a band-stop filter).
       Used to remove one specific interference frequency.

OSFI — One Shot Falling Input (FBD)
       The FBD version: generates a one-shot pulse for a single scan when the
       input goes from 1 to 0.

OSRI — One Shot Rising Input (FBD)
       The FBD version: generates a one-shot pulse for a single scan when the
       input goes from 0 to 1.

PI   — Proportional + Integral
       A simple PI controller (without derivative action).
       Suited to loops where derivative action is not wanted.

PMUL — Pulse Multiplier
       Generates output pulses at a rate proportional to the input signal.
       Used to drive stepping valves.

POSP — Position Proportional
       Controls a positioning valve. Produces Open and Close signals from the
       difference between the setpoint (SP) and the current position.

RESD — Reset Dominant
       An SR flip-flop in which Reset takes priority over Set.
       When both are active the output is cleared.

RLIM — Rate Limiter
       Limits the rate of change of the output signal. The ByPass input lets a
       signal through unlimited.

RMPS — Ramp/Soak
       Runs a ramp/soak program: it ramps to each target value in turn and holds
       it there for the given time.

SCL  — Scale
       Linear scaling of an input value. Converts one range into another
       (for example 4-20 mA into 0-100%).

SCRV — S-Curve
       Generates an S-shaped curve for smooth acceleration and deceleration.

SEL  — Select
       Selects one of two inputs according to the selector.
       With SelectorIn = 0 the output is In1; with 1 it is In2.

SETD — Set Dominant
       An SR flip-flop in which Set takes priority over Reset.
       When both are active the output is set.

SNEG — Selectable Negate
       With NegateEnable = 1 the output is -input; with 0 the output is the input.

SOC  — Second Order Controller
       A second order controller for demanding control loops.

SRTP — Split Range Time Proportional
       Splits the control signal into heating and cooling and converts it into
       pulse width modulation (PWM).

SSUM — Selected Summer
       Sums up to 4 inputs, each with its own enable switch.

TOT  — Totalizer
       Accumulates the integral of the input signal over time. Used to total flow
       (litres over a period, for example). Supports start/stop/reset.

UPDN — Up/Down Accumulator (FBD)
       An analog accumulator: it adds InPlus and subtracts InMinus.

BTDT — Bit Field Distribute with Target (FBD)
       The FBD version of BTD, with an extra Target input that defines the
       bits left unchanged.
```

---

## SFC CONTROL (SEQUENTIAL FUNCTION CHART)

```
SFP  — SFC Pause
       Pauses or resumes the given SFC routine.
       target_state: Running, Paused.

SFR  — SFC Reset
       Resets an SFC routine and sets execution to the given step.

PSC  — Pause SFC
       Pauses the current SFC routine from step or transition logic.

PFL  — Pause for Fault Log
       Pauses the SFC and writes the fault into the fault log buffer.

PPD  — Pause Phase Delay
       Pauses the current SFC phase, allowing time for operations to finish.

PRNP — Pause Run Next Phase
       Pauses the current phase until the next phase is started.
```

---

## EQUIPMENT PHASE CONTROL

```
PATT — Phase Attach
       Attaches the calling program to the given Equipment Phase.
       This lets an outside program control the phase.

PCMD — Phase Command
       Sends a command (Start, Stop, Hold, Abort and others) to the given
       Equipment Phase.

PCLF — Phase Clear Failure
       Clears the failure record of the given Equipment Phase.

PDET — Phase Detach
       Detaches the calling program from the given Equipment Phase.

POVR — Phase Override Command
       Sends an override command (Hold, Stop, Abort) to the given Equipment Phase.
       It takes priority over the ordinary commands.

PXRQ — Phase External Request
       Raises an external request from the phase (for example a request to an
       outside sequencer such as FactoryTalk Batch).
```

---

## MOTION CONTROL INSTRUCTIONS

```
MSO  — Motion Servo On
       Turns the axis servo on. Enables the drive amplifier.

MSF  — Motion Servo Off
       Turns the axis servo off. Disables the drive amplifier.

MAH  — Motion Axis Home
       Starts the homing procedure for the axis.
       Modes: Active, Passive, Absolute.

MAJ  — Motion Axis Jog
       Starts continuous motion of the axis at a given speed and direction.
       The axis keeps moving until it is stopped.

MAM  — Motion Axis Move
       Moves the axis to a given position or by a given distance.
       Types: Absolute (to a position), Incremental (by a distance).

MAS  — Motion Axis Stop
       Stops motion of the axis. Types: Immediate, Fast Stop and Controlled
       (decelerating along a profile).

MASD — Motion Axis Shutdown
       Shuts the axis down — it drops control and removes power.

MASR — Motion Axis Shutdown Reset
       Clears the shutdown state of the axis, taking it from Shutdown back
       to Axis Ready.

MAG  — Motion Axis Gear
       Sets up an electronic master-slave link between two axes.
       The slave axis moves in proportion to the master at the given ratio.

MAPC — Motion Axis Position Cam
       Sets up a position cam link between a master and a slave axis.
       The profile is defined by an array of points.

MATC — Motion Axis Time Cam
       Sets up a time cam link. The slave axis follows a profile tied to time
       rather than to the position of the master axis.

MAR  — Motion Axis Registration
       Captures the axis position on an external event (a trigger).
       Used for precise synchronisation with external sensors.

MAW  — Motion Axis Watch Position
       Waits for the axis to reach a given position and raises an event.

MAOC — Motion Axis Output Cam
       Drives a digital output of the drive from a cam profile tied to the
       axis position.

MDOC — Motion Disarm Output Cam
       Disarms an output cam that was armed earlier.

MCD  — Motion Change Dynamics
       Changes the speed, acceleration and deceleration of an axis while it moves.

MRP  — Motion Redefine Position
       Redefines the current position of the axis without physical motion.

MDO  — Motion Direct Drive Output
       Applies voltage or current directly to the drive, bypassing the control loops.

MDF  — Motion Direct Drive Off
       Turns direct drive output mode off.

MDR  — Motion Direct Drive On
       Turns direct drive output mode on.

MDW  — Motion Dwell
       Holds the axis for a given time, maintaining its position.

MGS  — Motion Group Stop
       Stops every axis in the motion group.

MGSD — Motion Group Shutdown
       Shuts down every axis in the motion group.

MGSP — Motion Group Strobe Position
       Captures the current positions of every axis in the group at the same
       moment. Used for consistent position readings.

MGSR — Motion Group Shutdown Reset
       Clears the shutdown state of every axis in the group.

MAAT — Motion Apply Axis Tuning
       Applies the calculated tuning parameters (gains) to the axis servo loop.

MAFR — Motion Apply Frequency Response
       Applies frequency analysis data to the axis tuning.

MAHD — Motion Axis Hookup Diagnostics
       Runs a diagnostic test that checks the feedback wiring and the direction
       of travel of the axis.

MRHD — Motion Run Hookup Diagnostics
       Runs the full axis hookup diagnostic cycle.

MRAT — Motion Run Axis Tuning
       Runs the automatic tuning (autotuning) procedure for the axis servo loop.

MCCD — Motion Coordinated Change Dynamics
       Changes the speed, acceleration and deceleration of coordinated motion
       on the fly.

MCCM — Motion Coordinated Circular Move
       Performs a circular or arc move in a coordinate system.

MCCP — Motion Calculate Cam Profile
       Calculates a cam profile from the given points.

MCLM — Motion Coordinated Linear Move
       Performs a straight-line move in a coordinate system.

MCS  — Motion Coordinated Stop
       Stops coordinated motion of every axis in the coordinate system.

MCSD — Motion Coordinated Shutdown
       Shuts down every axis of the coordinate system.

MCSR — Motion Coordinated Shutdown Reset
       Clears the shutdown state of the coordinate system.

MCSV — Motion Calculate Slave Values
       Calculates the position, velocity and acceleration of the slave axis from
       a cam profile for a given master position.

MCT  — Motion Coordinated Transform
       Transforms coordinates between two coordinate systems — for example from
       the cartesian system of a robot into the axis system.

MCTP — Motion Calculate Transform Position
       Calculates a position in the target coordinate system from a position in
       the source system and the transform parameters.

MCR  — Master Control Reset
       In the ladder context: it brackets a zone of rungs, and while its condition
       is false every output inside the zone is cleared. Not to be confused with
       the Motion instructions.
```

---

## SAFETY INSTRUCTIONS

```
DCM  — Dual Channel Input Monitor
       Monitors two input channels for consistency. When the channels disagree
       for longer than the allowed time it produces a fault output.

DCS  — Dual Channel Stop
       Monitors two input channels and produces a safe stop output.
       Supports automatic and manual restart types.

DCSRT — Dual Channel Stop with Run/Test
       Like DCS, but with an extra Enable input for switching between run and
       test modes.

DCST — Dual Channel Stop with Test
       Like DCS, but with a test request input for periodic proof testing of the
       safety function.

DCSTM — Dual Channel Stop with Test and Mute
       DCST extended with muting — it allows the protection to be suspended
       while something passes through the safeguarded zone.

DCSTL — Dual Channel Stop with Test and Lock
       DCST extended with locking — for LOTO (Lockout/Tagout) procedures during
       equipment maintenance.

DIN  — Dual Input
       Monitors two input channels and checks them for consistency.
       Supports circuit reset and fault reset.

ENPEN — Enable Pendant
       Monitors the dual channel input of the Enable switch on a control pendant.

ESTOP — Emergency Stop
       Handles the dual channel input of an emergency stop button (E-STOP).
       Supports several restart types.

LC   — Light Curtain
       Monitors the dual channel input of a light curtain. Supports muting
       (suspending protection during a controlled pass) and input filtering.

RIN  — Redundant Input
       Monitors two redundant input channels and checks them for consistency.

ROUT — Redundant Output
       Drives two redundant output channels with feedback monitoring.

SMAT — Safety Mat
       Monitors the dual channel input of a safety mat.
       Detects short circuits in the wiring.

THRS — Two-Hand Run Station
       Monitors the two buttons of a two-hand station. The output is activated
       only while both buttons are pressed together.

THRSE — Two-Hand Run Station with Enable
       THRS extended with an Enable input and with monitoring of the discrepancy
       between the buttons.

EPMS — Eight-Point Mute Sensor
       Monitors up to 8 mute sensors with locking.
       Used for complex safeguarded zones with many sensors.

FPMS — Five-Point Mute Sensor
       Monitors up to 5 mute sensors with fault checking.

FSBM — Four-Sensor Bidirectional Mute
       Controls light curtain muting with 4 sensors that determine the direction
       an object travels through the safeguarded zone.

TSAM — Two-Sensor Asymmetric Mute
       Controls muting with 2 sensors for a one-way pass.

TSSM — Two-Sensor Symmetric Mute
       Controls muting with 2 sensors for a two-way pass.
       The sensors sit symmetrically about the curtain.

CSM  — Configurable Safety Module
       A general purpose safety block. It monitors two channels and checks the
       mechanical delay and the maximum pulse period.
```

---

## PRESS CONTROL INSTRUCTIONS (SAFETY)

```
AVC  — Anti-Repeat Valve Control
       Controls a press valve with feedback monitoring, preventing a repeat
       stroke when the valve jams.

CBCM — Continuous Barrier Control Module
       Press control in continuous mode with barrier guarding and monitoring
       of the slide zone.

CBIM — Continuous Barrier Inch Module
       Inch mode press control with barrier guarding and inch time monitoring.

CBSSM — Continuous Barrier Single Stroke Module
       Single stroke press control with barrier guarding.

CPM  — Cam Press Monitor
       Monitors the slide position of a cam press against a cam profile.
       Supports brake, overrun and dynamic cams.

CROUT — Configurable Redundant Output
       Drives a redundant (dual channel) safety output with feedback monitoring
       and a configurable response time.

MMVC — Mechanical Motion Valve Control
       Monitors a mechanical press valve. It checks Enable, the mode switch,
       the limit switches and flywheel stop.

MVC  — Motion Valve Control
       Controls a press valve with two feedback channels and a configurable
       response time.
```

---

## USER-DEFINED BLOCKS (AOI)

```
AOI_SEQUENCE — Custom Sequence Controller
               A user-defined block (Add-On Instruction) for step-by-step process
               control. It holds the current step number, checks the transition
               conditions and drives the timers or commands of each stage of the
               sequence.
```

---
