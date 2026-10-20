/* Handover firmware: user selections are in this main.c.
 * ADC inputs, gain, reference, filters and technique defaults are grouped in
 * the labelled configuration function below. Start applies dashboard run values.
 */
#include <msp430.h>

#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>

#include "ad5940_lib/ad5940.h"
#include "support/support_functions.h"
#include "Amperometric/Amperometric.h"
// Startup defaults and UART limits, kept here for direct editing.

/*
 * User-facing constant-potential amperometry configuration.
 *
 * AMP_DETECTION_POTENTIAL_MV follows the laboratory convention E_WE - E_RE.
 * The Analog Devices amperometry application defines SensorBias as
 * V_RE - V_SE, so negate the requested potential exactly once here.
 */
#define AMP_DETECTION_POTENTIAL_MV 400
#define AMP_SENSOR_BIAS_MV (-AMP_DETECTION_POTENTIAL_MV)

/* Validated resistor-test acquisition settings. */
#define AMP_SAMPLE_PERIOD_MS 100u
#define AMP_MIN_SAMPLE_PERIOD_MS 50u    /* 20 Hz: leaves UART headroom */
#define AMP_MAX_SAMPLE_PERIOD_MS 10000u /* 0.1 Hz */
#define AMP_RTIA_NOMINAL_OHM 10000u
#define AMP_VZERO_MV 1100u

/* Versioned UART contract consumed by tools/amp_dashboard.py. */
#define AMP_UART_SCHEMA "MSP430_AMP_UART_V4"
#define AMP_UART_BAUD 9600u

#include "ec_protocol.h"
#include "ec_control.inc"
static int32_t amp_potential_mv = AMP_DETECTION_POTENTIAL_MV;

#define APPBUFF_SIZE 100
uint32_t AppBuff[APPBUFF_SIZE];
float LFOSCFreq;
static uint32_t amp_sample_index = 0u;
static uint32_t amp_isr_error_count = 0u;
static uint32_t amp_target_samples = 0u;
static uint32_t amp_run_number = 0u;
static uint32_t amp_sample_period_ms = AMP_SAMPLE_PERIOD_MS;
static BoolFlag amp_running = bFALSE;
static BoolFlag amp_platform_ready = bFALSE;
volatile uint8_t amp_metadata_requested = 0u;
volatile uint8_t amp_start_requested = 0u;
volatile uint8_t amp_stop_requested = 0u;
volatile uint32_t amp_requested_duration_ms = 0u;
volatile uint32_t amp_requested_sample_period_ms = AMP_SAMPLE_PERIOD_MS;

/* Report the active settings and firmware identity to the dashboard. */
static void AMPShowMetadata(void);

#define LED_PIN BIT0 // FOR DEBUGGING PURPOSES

/////////////////////////////////////////// Definitions
//////////////////////////////////////////////////////
// P8.3 in MCU connected to AD5941 CS
#define AD5941_CS_DIR P8DIR
#define AD5941_CS_OUT P8OUT
#define AD5941_CS_PIN BIT3

// P3.0 connected to AD5941 RESET
#define AD5941_RST_DIR P3DIR
#define AD5941_RST_OUT P3OUT
#define AD5941_RST_PIN BIT0

// P4.1 in MCU connected to AD5941 D0 GPIO (Interrupt)
#define AD5941_INT_DIR P4DIR
#define AD5941_INT_OUT P4OUT
#define AD5941_INT_PIN BIT1

// My delay counters
unsigned int d_counter_1 = 0;
unsigned int d_counter_2 = 0;
char test_array[20] = {0};

///////////////////////////////////////////////////////////////////////////// Support functions
// function: provide some delay using loops
void myDelay()
{
    d_counter_1 = 0;
    while (d_counter_1 < 20)
    {
        d_counter_2 = 0;
        while (d_counter_2 < 65000)
        {
            d_counter_2++;
        } // end while
        d_counter_1++;
    } // end while
} // end function

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// Back Channel UART Initialisation (MSP430 and PC)
// function: setup UART (UCA0) to send chars at 9600 baud rate, 8 data bits, 1 stop bit
void initUart()
{
    UCA0CTLW0 = UCSWRST;                       // Put eUSCI in reset
    UCA0CTLW0 |= UCSSEL__SMCLK;                // Select SMCLK
    UCA0BRW = 104;                             // Baud rate divisor for 9600 baud at 16 MHz
    UCA0MCTLW = UCOS16 | 0xD600 | (0x02 << 4); // UCBRSx=0xD6, UCBRFx=2
    UCA0CTLW0 &= ~UCSWRST;                     // Initialize eUSCI
    UCA0IE |= UCRXIE;                          // Enable USCI_A0 RX interrupt

} // end function

// function: write a char to UART (UCA1)
void writeChar(char b)
{
    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = b;
} // end function

// function: write a new line to UART
void writeNewLine()
{
    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = '\n';

    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = '\r';
} // end function

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// GPIO Configuration and Initialisation
/////////////// function: setup the GPIO pins as required by the design
void initGPIO()
{
    ///////////////////////// Configure UART pins (UCA0 in UART mode)
    // P2.0 - UCA0TXD
    // P2.1 - UCA0RXD
    P2SEL0 &= ~(BIT0 | BIT1); // Clear P2SEL0 for pins 0 and 1
    P2SEL1 |= BIT0 | BIT1;    // Set P2SEL1 for pins 0 and 1

    /////////////////////////////////////////////////////////////////////

    // Configure P1.0 and P1.1 as outputs (LEDs) FOR DEBUGGING
    P1DIR |= BIT0 | BIT1;
    P1OUT &= ~(BIT0 | BIT1); // Initially turn off P1.0 and P1.1

    //    // ---- Analogue switch control: P2.3 ----  ///// new
    P2SEL0 &= ~BIT3;
    P2SEL1 &= ~BIT3;
    P2DIR |= BIT3;

    P2OUT |= BIT3;

    /////////////////////// Disable the GPIO power-on default high-impedance mode to activate
    /// previously configured port settings
    PM5CTL0 &= ~LOCKLPM5;

} // end function

/////////////////////// function: set clock to 16 MHz
void initClockTo16MHz()
{
    // Configure one FRAM wait for the state as required by the datasheet for MCLK
    // operation beyond 8 MHz _before_ configuring the clock system.
    FRCTL0 = FRCTLPW | NWAITS_1;

    // Configure the XTAL input pins
    PJSEL0 |= BIT4 + BIT5;

    // Clock System Setup
    CSCTL0_H = CSKEY >> 8;        // Unlock CS registers (move the CS KEY up 8 bits)
    CSCTL1 = DCORSEL | DCOFSEL_4; // Set DCO to 16MHz
    CSCTL2 = SELA__LFXTCLK | SELS__DCOCLK | SELM__DCOCLK;
    CSCTL3 = DIVA_0 | DIVS__1 | DIVM__1; // Set all dividers

    //    CSCTL4 &= ~LFXTOFF; //enable the ACLK
    //    do{
    //        CSCTL5 &= ~LFXTOFFG;                  // Clear XT1 fault flag
    //        SFRIFG1 &= ~OFIFG;
    //    }while (SFRIFG1&OFIFG);                   // Test oscillator fault flag

    CSCTL0_H = 0; // Lock CS registers
} // end function

///////////////////////////////////////////////// Basic AD5940 configuration
static int32_t AD5940PlatformCfg(void)
{
    CLKCfg_Type clk_cfg;
    FIFOCfg_Type fifo_cfg;
    AGPIOCfg_Type gpio_cfg;

    // SEQCfg_Type seq_cfg;

    LFOSCMeasure_Type LfoscMeasure;

    // Apply hardware reset
    AD5940_HWReset();

    // Apply platform configuration sequence
    AD5940_Initialize();

    ////////////////////////////////////// Step1. Configure the clock
    clk_cfg.HFOSCEn = bTRUE;
    clk_cfg.HFXTALEn = bFALSE;
    clk_cfg.LFOSCEn = bTRUE;
    clk_cfg.HfOSC32MHzMode = bFALSE;
    clk_cfg.SysClkSrc = SYSCLKSRC_HFOSC;
    clk_cfg.SysClkDiv = SYSCLKDIV_1;
    clk_cfg.ADCCLkSrc = ADCCLKSRC_HFOSC;
    clk_cfg.ADCClkDiv = ADCCLKDIV_1;
    AD5940_CLKCfg(&clk_cfg);

    /////////////////////////////////////// Step2. Configure FIFO and Sequencer
    fifo_cfg.FIFOEn = bFALSE;
    fifo_cfg.FIFOMode = FIFOMODE_FIFO;
    fifo_cfg.FIFOSize = FIFOSIZE_4KB; // 4kB for FIFO, The rest 2kB for sequencer
    fifo_cfg.FIFOSrc = FIFOSRC_DFT;
    fifo_cfg.FIFOThresh = 4;
    AD5940_FIFOCfg(&fifo_cfg); // Disable to reset FIFO
    fifo_cfg.FIFOEn = bTRUE;
    AD5940_FIFOCfg(&fifo_cfg);

    ////////////////////////////////////////// Configure the sequencer and stop it
    /*
    seq_cfg.SeqMemSize = SEQMEMSIZE_2KB;
    seq_cfg.SeqBreakEn = bFALSE;
    seq_cfg.SeqIgnoreEn = bFALSE;
    seq_cfg.SeqCntCRCClr = bTRUE;
    seq_cfg.SeqEnable = bFALSE;
    seq_cfg.SeqWrTimer = 0;
    AD5940_SEQCfg(&seq_cfg);
    */

    /////////////////////////////////////////// Step3. Interrupt controller
    AD5940_INTCCfg(
        AFEINTC_1, AFEINTSRC_ALLINT,
        bTRUE); /* Enable all interrupt in Interrupt Controller 1, so we can check INTC flags */
    AD5940_INTCCfg(
        AFEINTC_0, AFEINTSRC_DATAFIFOTHRESH,
        bTRUE); /* Interrupt Controller 0 will control GP0 to generate interrupt to MCU */
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);

    /////////////////////////////////////////// Step4: Reconfigure GPIO
    gpio_cfg.FuncSet = GP6_SYNC | GP5_SYNC | GP4_SYNC | GP2_SYNC | GP1_SLEEP | GP0_INT;
    gpio_cfg.InputEnSet = 0;
    gpio_cfg.OutputEnSet =
        AGPIO_Pin0 | AGPIO_Pin1 | AGPIO_Pin4 | AGPIO_Pin5 | AGPIO_Pin6 | AGPIO_Pin2;
    gpio_cfg.OutVal = 0;
    gpio_cfg.PullEnSet = 0;
    AD5940_AGPIOCfg(&gpio_cfg);

    // AD5940_SleepKeyCtrlS(SLPKEY_UNLOCK);          // Allow AFE to enter sleep mode

    /* Measure LFOSC frequency */
    LfoscMeasure.CalDuration = 1000.0; // 1000 ms used for calibration
    LfoscMeasure.CalSeqAddr = 0;
    LfoscMeasure.SystemClkFreq = 16000000.0f; // 16 MHz in this firmware
    if (AD5940_LFOSCMeasure(&LfoscMeasure, &LFOSCFreq) != AD5940ERR_OK)
        return AD5940ERR_TIMEOUT;

    return 0;
} // end

// Change the application parameters here if you want to change it to a non-default value
/* ==================== EXPERIMENT DEFAULT CONFIGURATION ==================
 * Edit ADC inputs/gain/reference/RTIA here, like the original examples.
 * The following parameters set startup defaults; Start applies the dashboard
 * run settings. Keep bias conversion and acquisition safety checks intact.
 */
void AD5940AMPStructInit(void)
{
    AppAMPCfg_Type *pAMPCfg;
    AppAMPGetCfg(&pAMPCfg);

    ///////////////////////////////////////////////////////////////////////// ADC INPUT CONFIG
    // Keep ONE positive and ONE negative input assignment active.
    // Current conversion requires the established TIA path below.
    // Internal-voltage tests belong in OCP: changing this path is no longer
    // an electrode-current measurement even if the graph still says microamps.

    ////////////// Positive Input
    // pAMPCfg->ADCMuxP = ADCMUXP_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pAMPCfg->ADCMuxP = ADCMUXP_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pAMPCfg->ADCMuxP = ADCMUXP_AIN4; // Established current signal

    ////////////// Negative Input
    // pAMPCfg->ADCMuxN = ADCMUXN_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pAMPCfg->ADCMuxN = ADCMUXN_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pAMPCfg->ADCMuxN = ADCMUXN_VZERO0; // Established current reference

    ////////////// ADC Set Gain
    // pAMPCfg->ADCPgaGain = ADCPGA_1;
    pAMPCfg->ADCPgaGain = ADCPGA_1P5; // 1.5x

    ////////////// ADC Reference
    pAMPCfg->ADCRefVolt = 1.82; // Volts; measure VREF_1V8 if calibrating

    ////////////// TIA Gain Resistor
    // pAMPCfg->LptiaRtiaSel = LPTIARTIA_20K;
    pAMPCfg->LptiaRtiaSel = LPTIARTIA_10K; // Nominal 10 kOhm; calibrated at startup

    ///////////////////////////////////////////////////////////////////////// ADC FILTER CONFIG
    // pAMPCfg->ADCSinc3Osr = ADCSINC3OSR_2;
    pAMPCfg->ADCSinc3Osr = ADCSINC3OSR_4;
    // pAMPCfg->ADCSinc2Osr = ADCSINC2OSR_1333;
    pAMPCfg->ADCSinc2Osr = ADCSINC2OSR_22; // Preserve the existing AMP filter

    ///////////////////////////////////////////////////////////////////////// EXPERIMENT PARAMETERS

    /* ADC gain is shared with conversion and calibration. */
    pAMPCfg->WuptClkFreq = LFOSCFreq;

    // Configure general parameters
    pAMPCfg->SeqStartAddr = 0;
    pAMPCfg->MaxSeqLen = 100; // @todo add checker in function

    pAMPCfg->RcalVal = 200.0; // 200.0
    pAMPCfg->NumOfData = -1;  // Continue until explicitly stopped

    // Configure the measurement parameters
    pAMPCfg->AmpODR = (float)AMP_SAMPLE_PERIOD_MS / 1000.0f;

    pAMPCfg->FifoThresh = 1; // the number of measurements aquired before alerting the host MCU

    pAMPCfg->SensorBias = (float)AMP_SENSOR_BIAS_MV;

    pAMPCfg->LpTiaRl = LPTIARLOAD_10R;

    pAMPCfg->Vzero = AMP_VZERO_MV; // Internal common-mode voltage in mV

} // end function

///////////////////////////////////////////////////////// display the result in UART
int32_t AMPShowResult(float *pData, uint32_t DataCount)
{
    uint32_t i;
    for (i = 0u; i < DataCount; ++i)
    {
        if ((amp_target_samples > 0u) && (amp_sample_index >= amp_target_samples))
        {
            break;
        }
        uint32_t scheduled_ms = amp_sample_index * amp_sample_period_ms;
        writeStringUART("AMP,");
        writeUIntUART(amp_sample_index);
        writeChar(',');
        writeUIntUART(scheduled_ms);
        writeChar(',');
        writeFloatUART(pData[i]);
        amp_sample_index++;
    }
    return 0;
} // end

static void AMPShowState(const char *state)
{
    writeStringUART("# AMP_STATE,");
    writeStringUART(state);
    writeNewLine();
}

static void AMPStopMeasurement(const char *reason)
{
    if (amp_running == bTRUE)
    {
        AppAMPCtrl(AMPCTRL_SHUTDOWN, 0);
        P2OUT |= BIT3;
        AD5940_ClrMCUIntFlag();
        amp_running = bFALSE;
        writeStringUART("# AMP_RUN_END,");
        writeStringUART(reason);
        writeChar(',');
        writeUIntUART(amp_sample_index);
        writeNewLine();
    }
    AMPShowState("IDLE");
}

static AD5940Err AMPStartMeasurement(uint32_t duration_ms, uint32_t requested_sample_period_ms)
{
    AD5940Err error;
    AppAMPCfg_Type *pAMPCfg;
    if (amp_platform_ready != bTRUE)
    {
        writeStringUART("# AMP_ERROR,PLATFORM_NOT_READY");
        writeNewLine();
        AMPShowState("FAULT");
        return AD5940ERR_TIMEOUT;
    }

    if (requested_sample_period_ms == 0u)
    {
        requested_sample_period_ms = AMP_SAMPLE_PERIOD_MS;
    }
    if ((requested_sample_period_ms < AMP_MIN_SAMPLE_PERIOD_MS) ||
        (requested_sample_period_ms > AMP_MAX_SAMPLE_PERIOD_MS))
    {
        writeStringUART("# AMP_ERROR,INVALID_SAMPLE_PERIOD_MS,");
        writeUIntUART(requested_sample_period_ms);
        writeNewLine();
        AMPShowState(!amp_platform_ready ? "FAULT" : amp_running == bTRUE ? "RUNNING" : "IDLE");
        return AD5940ERR_PARA;
    }

    if (amp_running == bTRUE)
    {
        AMPStopMeasurement("RESTARTED");
    }

    amp_sample_index = 0u;
    amp_target_samples = 0u;
    amp_sample_period_ms = requested_sample_period_ms;
    if (duration_ms > 0u)
    {
        amp_target_samples = duration_ms / amp_sample_period_ms;
        if ((duration_ms % amp_sample_period_ms) != 0u)
        {
            amp_target_samples++;
        }
    }

    AppAMPGetCfg(&pAMPCfg);
    pAMPCfg->AmpODR = (float)amp_sample_period_ms / 1000.0f;
    pAMPCfg->SensorBias = -(float)amp_potential_mv;
    pAMPCfg->bParaChanged = bTRUE;

    /* AppAMPInit applies the configured electrode bias. Starting it only after
     * the dashboard command keeps the cell unbiased while the firmware waits,
     * allowing the first acquired points to contain the initial transient. */
    P2OUT &= ~BIT3;
    error = AppAMPInit(AppBuff, APPBUFF_SIZE);
    if (error != AD5940ERR_OK)
    {
        writeStringUART("# AMP_ERROR,START_INITIALIZATION,");
        writeUIntUART((uint32_t)error);
        writeNewLine();
        AppAMPCtrl(AMPCTRL_SHUTDOWN, 0);
        P2OUT |= BIT3;
        AMPShowState("IDLE");
        return error;
    }

    AD5940_ClrMCUIntFlag();
    error = AppAMPCtrl(AMPCTRL_START, 0);
    if (error != AD5940ERR_OK)
    {
        writeStringUART("# AMP_ERROR,START_CONTROL,");
        writeUIntUART((uint32_t)error);
        writeNewLine();
        AppAMPCtrl(AMPCTRL_SHUTDOWN, 0);
        P2OUT |= BIT3;
        AMPShowState("IDLE");
        return error;
    }

    amp_run_number++;
    amp_running = bTRUE;
    writeStringUART("# AMP_RUN_BEGIN,");
    writeUIntUART(amp_run_number);
    writeChar(',');
    writeUIntUART(duration_ms);
    writeNewLine();
    AMPShowMetadata();
    AMPShowState("RUNNING");
    return AD5940ERR_OK;
}

/* Report the active settings and firmware identity to the dashboard. */
static void AMPShowMetadata(void)
{
    AppAMPCfg_Type *pAMPCfg;
    AppAMPGetCfg(&pAMPCfg);

    writeStringUART("# AMP_META_BUILD_ID,HANDOVER_2026_10_13_AMP_RESTART_R4");
    writeNewLine();
    writeStringUART("# AMP_META_SCHEMA,");
    writeStringUART(AMP_UART_SCHEMA);
    writeNewLine();
    writeStringUART("# AMP_META_POTENTIAL_MV,");
    writeFloatUART((float)amp_potential_mv);
    writeStringUART("# AMP_META_INTERNAL_SENSOR_BIAS_MV,");
    writeFloatUART(pAMPCfg->SensorBias);
    writeStringUART("# AMP_META_SAMPLE_PERIOD_MS,");
    writeUIntUART(amp_sample_period_ms);
    writeNewLine();
    writeStringUART("# AMP_META_RTIA_NOMINAL_OHM,");
    writeUIntUART(AMP_RTIA_NOMINAL_OHM);
    writeNewLine();
    writeStringUART("# AMP_META_RTIA_CALIBRATED_OHM,");
    writeFloatUART(pAMPCfg->RtiaCalValue.Magnitude);
    writeStringUART("# AMP_META_VZERO_MV,");
    writeUIntUART(AMP_VZERO_MV);
    writeNewLine();
    writeStringUART("# AMP_META_LFOSC_HZ,");
    writeFloatUART(LFOSCFreq);
    writeStringUART("# AMP_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE");
    writeNewLine();
    writeStringUART("# AMP_META_CURRENT_CONVENTION,ANODIC_POSITIVE");
    writeNewLine();
    writeStringUART("# AMP_META_CURRENT_UNITS,uA");
    writeNewLine();
    writeStringUART("# AMP_META_TIME_UNITS,ms");
    writeNewLine();
    writeStringUART("# AMP_META_TIME_SOURCE,AD5941_WUPT_SCHEDULE");
    writeNewLine();
    writeStringUART("# AMP_META_CONTROL_MODE,DASHBOARD_TIMED_RATE_RUN");
    writeNewLine();
    writeStringUART("# AMP_META_UART_BAUD,");
    writeUIntUART(AMP_UART_BAUD);
    writeNewLine();
}

// int32_t AMPShowResult(float *pData, uint32_t DataCount)
//{
//     uint32_t w = ((uint32_t*)pData)[0];
//     union { uint32_t u; float f; } v;
//     v.u = w;
//
//     writeStringUART("w=0x");
//     writeHex32UART(w);
//     writeStringUART(" f_bits=0x");
//     writeHex32UART(v.u);
//     writeStringUART(" ");
//     // print sign/exponent/mantissa quickly:
//     writeStringUART("exp=");
//     writeUIntUART((w >> 23) & 0xFF);
//     writeStringUART(" ");
//
//     writeNewLine();
//     return 0;
// }

///////////////////////////////////////////////////////////////////////////

int main(void)
{
    ///////////////////////// Stop the watch-dog timer
    WDTCTL = WDTPW | WDTHOLD;

    ///////////////////////// setup the system clock to 16 MHz
    initClockTo16MHz();

    initGPIO();

    initUart();

    P2OUT |= BIT3;

    writeStringUART("# AMP_BOOT,MSP430FR5994_AD5941_AMP");
    writeNewLine();

    writeStringUART("# AMP_META_BUILD_ID,HANDOVER_2026_10_13_AMP_RESTART_R4");
    writeNewLine();
    writeStringUART("# AMP_META_SCHEMA,");
    writeStringUART(AMP_UART_SCHEMA);
    writeNewLine();
    writeStringUART("# AMP_META_UART_BAUD,9600");
    writeNewLine();
    AMPShowState("INITIALIZING");

    // setup the MCU resources for connecting with AD5940
    AD5940_MCUResourceInit(0);
    AD5940_Delay10us(50000); // delay 500 m

    // call platform configuration
    amp_platform_ready = (AD5940PlatformCfg() == AD5940ERR_OK);
    if (!amp_platform_ready)
    {
        writeStringUART("# AMP_ERROR,LFOSC_INITIALIZATION_TIMEOUT");
        writeNewLine();
    }
    AD5940_Delay10us(50000); // delay 500 m

    AD5940AMPStructInit();   // Configure your parameters in this function
    AD5940_Delay10us(50000); // delay 500 m

    /* Remain unbiased and idle until the dashboard sends S,<duration_ms>. */
    AMPShowMetadata();
    AMPShowState(amp_platform_ready ? "IDLE" : "FAULT");

    /////////////////////////////////////////////////////// LOOP
    for (;;)
    {
        char command;
        uint8_t argc;
        int32_t args[EC_MAX_ARGS];
        if (ec_stop)
        {
            ec_stop = 0;
            amp_stop_requested = 1;
            amp_start_requested = 0;
        }
        if (ec_bad_command)
        {
            ec_bad_command = 0;
            writeStringUART("# AMP_ERROR,INVALID_OR_BUSY_COMMAND");
            writeNewLine();
        }
        if (ec_take(&command, &argc, args))
        {
            if (command == 'M')
                amp_metadata_requested = 1;
            else if (command == 'S')
            {
                int32_t period = argc >= 2 ? args[1] : AMP_SAMPLE_PERIOD_MS;
                int32_t potential = argc >= 3 ? args[2] : amp_potential_mv;
                if (argc < 1 || argc > 3 || args[0] < 0 || period < AMP_MIN_SAMPLE_PERIOD_MS ||
                    period > AMP_MAX_SAMPLE_PERIOD_MS || potential < -800 || potential > 800 ||
                    amp_running)
                {
                    writeStringUART("# AMP_ERROR,INVALID_PARAMETERS_OR_RUNNING");
                    writeNewLine();
                }
                else
                {
                    amp_potential_mv = potential;
                    amp_requested_duration_ms = (uint32_t)args[0];
                    amp_requested_sample_period_ms = (uint32_t)period;
                    amp_start_requested = 1;
                }
            }
        }
        if (amp_metadata_requested != 0u)
        {
            amp_metadata_requested = 0u;
            AMPShowMetadata();
            AMPShowState(!amp_platform_ready ? "FAULT" : amp_running == bTRUE ? "RUNNING" : "IDLE");
        }

        if (amp_stop_requested != 0u)
        {
            amp_stop_requested = 0u;
            AMPStopMeasurement("USER_STOP");
        }

        if (amp_start_requested != 0u)
        {
            uint32_t requested_duration_ms = amp_requested_duration_ms;
            uint32_t requested_sample_period_ms = amp_requested_sample_period_ms;
            amp_start_requested = 0u;
            AMPStartMeasurement(requested_duration_ms, requested_sample_period_ms);
        }

        if ((amp_running == bTRUE) && AD5940_GetMCUIntFlag())
        {
            uint32_t temp = APPBUFF_SIZE;
            AD5940_ClrMCUIntFlag();

            if (AppAMPISR(AppBuff, &temp) != AD5940ERR_OK)
            {
                amp_isr_error_count++;
                writeStringUART("# AMP_ERROR,ISR,");
                writeUIntUART(amp_isr_error_count);
                writeNewLine();
                AMPStopMeasurement("ACQUISITION_ERROR");
            }
            else if (temp > 0u)
            {
                AMPShowResult((float *)AppBuff, temp);
                if ((amp_target_samples > 0u) && (amp_sample_index >= amp_target_samples))
                {
                    AMPStopMeasurement("COMPLETED");
                }
            }
        }

    } // end for
} // end main

/////////////////////////////////////////////////////////////////////////// Previous Method Without
/// Interrupt
//    // start the sequencer
//    AD5940_SEQMmrTrig(SEQID_0);
//    AD5940_Delay10us(50000);    // delay 500 m
//
//   for(;;){
//
//       while (1)
//       {
//           // poll the INTCFLAG register to wait for Data FIFO threshold interrupt.
//           if(AD5940_INTCTestFlag(AFEINTC_0, AFEINTSRC_DATAFIFOTHRESH) == bTRUE)
//           {
//               break;
//           }
//           else
//           {
//               // do nothing
//               AD5940_Delay10us(100000);    // delay 1 second
//           }
//       }//end while
//
//    /// display data
//    uint32_t temp = APPBUFF_SIZE;
//    AppAMPISR(AppBuff, &temp);
//    AMPShowResult((float*)AppBuff, temp);
//
//
//    } //end for
//
//    return 0;
//
//} //end main
