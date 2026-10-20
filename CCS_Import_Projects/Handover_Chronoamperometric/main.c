/* Handover firmware: user selections are in this main.c.
 * ADC inputs, gain, reference, filters and technique defaults are grouped in
 * the labelled configuration function below. Start applies dashboard run values.
 */
#include <msp430.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdbool.h>

#include <math.h>

#include "ad5940_lib/ad5940.h"
#include "support/support_functions.h"
#include "ec_protocol.h"
#include "ec_control.inc"
#include "Chrono/ChronoAmperometric.h"

#define APPBUFF_SIZE 256
#define CA_MAX_SAMPLES 10000UL
/* Acquire at the filter rate, then transmit on the two-wire MSP-FET UART.
 * FRAM storage avoids blocking ADC acquisition on slow serial output.
 * Each Start overwrites this buffer; retained values are not a saved session. */
#pragma PERSISTENT(ca_samples)
static float ca_samples[CA_MAX_SAMPLES] = {0};
static uint32_t ca_count = 0;

#define LED_PIN BIT0

uint32_t AppBuff[APPBUFF_SIZE];
float LFOSCFreq;
static uint8_t ca_running = 0;
static uint8_t ca_platform_ready = 0;
static uint32_t ca_index = 0;
static int32_t ca_base_mv = 0, ca_step_mv = 400;
static uint32_t ca_duration_ms = 1000;
#define CA_SAMPLE_HZ (800000.0f / 4.0f / 1333.0f)

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
// function: setup UART (UCA1) to send chars at 9600 baud rate, 8 data bits, 1 stop bit
void initUart()
{

    UCA0CTLW0 = UCSWRST;                       // Put eUSCI in reset
    UCA0CTLW0 |= UCSSEL__SMCLK;                // Select SMCLK
    UCA0BRW = 104;                               // Baud rate calculation for 9600 baud
    UCA0MCTLW = UCOS16 | 0xD600 | (0x02 << 4); // Modulation parameters
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
/////////////// Function: setup the GPIO pins as required by the design
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

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// MCU Clock
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

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// AD5941 Chrono Initialisation

/* Initialize AD5940 basic blocks like clock */
static int32_t AD5940PlatformCfg(void)
{
    CLKCfg_Type clk_cfg;
    FIFOCfg_Type fifo_cfg;
    AGPIOCfg_Type gpio_cfg;
    LFOSCMeasure_Type LfoscMeasure;

    /* Use hardware reset */
    AD5940_HWReset();

    /* Platform configuration */
    AD5940_Initialize();

    /* Step1. Configure clock */
    clk_cfg.ADCClkDiv = ADCCLKDIV_1;
    clk_cfg.ADCCLkSrc = ADCCLKSRC_HFOSC;
    clk_cfg.SysClkDiv = SYSCLKDIV_1;
    clk_cfg.SysClkSrc = SYSCLKSRC_HFOSC;
    clk_cfg.HfOSC32MHzMode = bFALSE;
    clk_cfg.HFOSCEn = bTRUE;
    clk_cfg.HFXTALEn = bFALSE;
    clk_cfg.LFOSCEn = bTRUE;
    AD5940_CLKCfg(&clk_cfg);

    /* Step2. Configure FIFO and Sequencer*/
    fifo_cfg.FIFOEn = bFALSE;
    fifo_cfg.FIFOMode = FIFOMODE_FIFO;
    fifo_cfg.FIFOSize = FIFOSIZE_4KB; /* 4kB for FIFO, The reset 2kB for sequencer */
    fifo_cfg.FIFOSrc = FIFOSRC_DFT;
    fifo_cfg.FIFOThresh = 4; // AppAMPCfg.FifoThresh;        /* DFT result. One pair for RCAL,
                             // another for Rz. One DFT result have real part and imaginary part */
    AD5940_FIFOCfg(&fifo_cfg); /* Disable to reset FIFO. */
    fifo_cfg.FIFOEn = bTRUE;
    AD5940_FIFOCfg(&fifo_cfg); /* Enable FIFO here */

    /* Step3. Interrupt controller */
    AD5940_INTCCfg(
        AFEINTC_1, AFEINTSRC_ALLINT,
        bTRUE); /* Enable all interrupt in Interrupt Controller 1, so we can check INTC flags */
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);

    AD5940_INTCCfg(AFEINTC_0, AFEINTSRC_DATAFIFOTHRESH | AFEINTSRC_ENDSEQ | AFEINTSRC_DATAFIFOOF,
                   bTRUE);
    /* Interrupt Controller 0 will control GP0 to generate interrupt to MCU */ //|AFEINTSRC_ENDSEQ
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);

    /* Step4: Reconfigure GPIO */
    gpio_cfg.FuncSet = GP6_SYNC | GP5_SYNC | GP4_SYNC | GP2_TRIG | GP1_SYNC | GP0_INT;
    gpio_cfg.InputEnSet = AGPIO_Pin2;
    gpio_cfg.OutputEnSet = AGPIO_Pin0 | AGPIO_Pin1 | AGPIO_Pin4 | AGPIO_Pin5 | AGPIO_Pin6;
    gpio_cfg.OutVal = 0;
    gpio_cfg.PullEnSet = 0;
    AD5940_AGPIOCfg(&gpio_cfg);
    AD5940_SleepKeyCtrlS(SLPKEY_UNLOCK); /* Enable AFE to enter sleep mode. */
    /* Measure LFOSC frequency */
    LfoscMeasure.CalDuration = 1000.0; /* 1000ms used for calibration. */
    LfoscMeasure.CalSeqAddr = 0;
    LfoscMeasure.SystemClkFreq = 16000000.0f; /* 16MHz in this firmware. */
    if (AD5940_LFOSCMeasure(&LfoscMeasure, &LFOSCFreq) != AD5940ERR_OK)
        return AD5940ERR_TIMEOUT;

    return 0;
}

/////////////////////////////////////////////////////////////////////////////////////////////
/// Parameters for Application
/* !!Change the application parameters here if you want to change it to none-default value */
/* ==================== EXPERIMENT DEFAULT CONFIGURATION ==================
 * Edit ADC inputs/gain/reference/RTIA here, like the original examples.
 * The following parameters set startup defaults; Start applies the dashboard
 * run settings. Keep bias conversion and acquisition safety checks intact.
 */
void AD5940AMPStructInit(void)
{
    AppCHRONOAMPCfg_Type *pAMPCfg;
    AppCHRONOAMPGetCfg(&pAMPCfg);

    ///////////////////////////////////////////////////////////////////////// ADC INPUT CONFIG
    // Keep ONE positive and ONE negative input assignment active.
    // Current conversion requires the established TIA path below.
    // Internal-voltage tests belong in OCP: changing this path is no longer
    // an electrode-current measurement even if the graph still says microamps.

    ////////////// Positive Input
    // pAMPCfg->ADCMuxP = ADCMUXP_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pAMPCfg->ADCMuxP = ADCMUXP_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pAMPCfg->ADCMuxP = ADCMUXP_LPTIA0_P; // Established current signal

    ////////////// Negative Input
    // pAMPCfg->ADCMuxN = ADCMUXN_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pAMPCfg->ADCMuxN = ADCMUXN_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pAMPCfg->ADCMuxN = ADCMUXN_LPTIA0_N; // Established current reference

    ////////////// ADC Set Gain
    // pAMPCfg->ADCPgaGain = ADCPGA_1;
    pAMPCfg->ADCPgaGain = ADCPGA_1P5; // 1.5x

    ////////////// ADC Reference
    pAMPCfg->ADCRefVolt = 1.82; // Volts; measure VREF_1V8 if calibrating

    ////////////// TIA Gain Resistor
    // pAMPCfg->LptiaRtiaSel = LPTIARTIA_20K;
    pAMPCfg->LptiaRtiaSel = LPTIARTIA_10K; // Nominal 10 kOhm; calibrated at startup

    ///////////////////////////////////////////////////////////////////////// EXPERIMENT PARAMETERS

    /* ADC gain is shared with conversion and calibration. */
    /* Configure general parameters */
    pAMPCfg->WuptClkFreq = LFOSCFreq; /* Use measured 32kHz clock freq for accurate wake up timer */
    pAMPCfg->SeqStartAddr = 0;
    pAMPCfg->MaxSeqLen = 512; /* @todo add checker in function */
    pAMPCfg->RcalVal = 200.0; // 10000.0;
    pAMPCfg->NumOfData = -1;  /* Never stop until you stop it manually by AppAMPCtrl() function */

    pAMPCfg->AmpODR = 1;
    pAMPCfg->FifoThresh = 16;
    pAMPCfg->ADCSinc3Osr = ADCSINC3OSR_4;
    pAMPCfg->ADCSinc2Osr = ADCSINC2OSR_1333;
    pAMPCfg->LpTiaRl = LPTIARLOAD_10R;

    pAMPCfg->ExtRtia = bFALSE;      /* Set to true if using external Rtia */
    pAMPCfg->ExtRtiaVal = 10000000; /* Enter external Rtia value here is using one */

    pAMPCfg->SensorBias = 0; /* Sensor bias voltage between reference and sense electrodes*/
    pAMPCfg->Vzero = 1100;   // was 1100

    /* Configure Pulse*/
    pAMPCfg->pulseAmplitude = 500; /* Pulse amplitude on counter electrode (mV) */
    pAMPCfg->pulseLength = 100;    /* Length of voltage pulse in ms */
}

static void CAState(void)
{
    writeStringUART("# CA_STATE,");
    writeStringUART(!ca_platform_ready ? "FAULT" : ca_running ? "RUNNING" : "IDLE");
    writeNewLine();
}
static void CAError(const char *reason)
{
    writeStringUART("# CA_ERROR,");
    writeStringUART(reason);
    writeNewLine();
}
/* Report the active settings and firmware identity to the dashboard. */
static void CAMetadata(void)
{
    writeStringUART("# CA_META_BUILD_ID,HANDOVER_2026_10_12_CA_BUFFERED_R3");
    writeNewLine();
    writeStringUART("# CA_META_SCHEMA,MSP430_CA_UART_V1");
    writeNewLine();
    writeStringUART("# CA_META_UART_BAUD,9600");
    writeNewLine();
    writeStringUART("# CA_META_TRANSFER_MODE,BUFFERED_AFTER_ACQUISITION");
    writeNewLine();
    writeStringUART("# CA_META_MAX_SAMPLES,10000");
    writeNewLine();
    writeStringUART("# CA_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE");
    writeNewLine();
    writeStringUART("# CA_META_CURRENT_CONVENTION,ANODIC_POSITIVE");
    writeNewLine();
    writeStringUART("# CA_META_CURRENT_UNITS,uA");
    writeNewLine();
    writeStringUART("# CA_META_TIME_SOURCE,AD5941_FILTER_GRID_RELATIVE_FIRST_SAMPLE");
    writeNewLine();
    writeStringUART("# CA_META_SAMPLE_RATE_HZ,");
    writeFloatUART(CA_SAMPLE_HZ);
    writeStringUART("# CA_META_BASE_MV,");
    writeIntUART(ca_base_mv);
    writeNewLine();
    writeStringUART("# CA_META_STEP_MV,");
    writeIntUART(ca_step_mv);
    writeNewLine();
    writeStringUART("# CA_META_DURATION_MS,");
    writeUIntUART(ca_duration_ms);
    writeNewLine();
}
static void CAStop(const char *reason)
{
    AD5940_SEQCtrlS(bFALSE);
    AppCHRONOAMPCtrl(CHRONOAMPCTRL_SHUTDOWN, 0);
    AD5940_ClrMCUIntFlag();
    P2OUT |= BIT3;
    if (ca_running)
    {
        writeStringUART("# CA_RUN_END,");
        writeStringUART(reason);
        writeChar(',');
        writeUIntUART(ca_index);
        writeNewLine();
    }
    ca_running = 0;
    CAState();
}
/* Stop electrode drive before sending buffered samples. UART speed changes
 * transfer time only; timestamps retain the original filtered ADC grid. */
static void CATransmit(const char *reason)
{
    uint32_t i;
    AD5940_SEQCtrlS(bFALSE);
    AppCHRONOAMPCtrl(CHRONOAMPCTRL_SHUTDOWN, 0);
    P2OUT |= BIT3;
    writeStringUART("# CA_TRANSFER_BEGIN,");
    writeUIntUART(ca_count);
    writeNewLine();
    for (i = 0; i < ca_count && !ec_stop; i++)
    {
        writeStringUART("CA,");
        writeUIntUART(ca_index);
        writeChar(',');
        writeFloatPlainUART((float)ca_index * 1000.0f / CA_SAMPLE_HZ);
        writeChar(',');
        writeFloatUART(ca_samples[i]);
        ca_index++;
    }
    if (ec_stop)
    {
        ec_stop = 0;
        CAStop("USER_STOP");
    }
    else CAStop(reason);
}
/* Validate and apply a potential step, including the initial hold. */
static void CAStart(int32_t *a, uint8_t argc)
{
    AppCHRONOAMPCfg_Type *cfg;
    AD5940Err error;
    uint32_t hold;
    if (!ca_platform_ready)
    {
        CAError("PLATFORM_NOT_READY");
        return;
    }
    if (argc != 4 || ca_running || a[0] < -800 || a[0] > 800 || a[1] < -800 || a[1] > 800 ||
        a[0] == a[1] || a[2] < 10 || a[2] > 60000 || a[3] < 0 || a[3] > 60000)
    {
        CAError("INVALID_PARAMETERS_OR_RUNNING");
        return;
    }
    ca_base_mv = a[0];
    ca_step_mv = a[1];
    ca_duration_ms = (uint32_t)a[2];
    ca_index = 0;
    ca_count = 0;
    AppCHRONOAMPGetCfg(&cfg);
    cfg->SensorBias = -(float)ca_base_mv;
    cfg->pulseAmplitude = -(float)(ca_step_mv - ca_base_mv);
    cfg->pulseLength = ca_duration_ms;
    cfg->bParaChanged = bTRUE;
    cfg->EndSeq = bFALSE;
    cfg->StopRequired = bFALSE;
    P2OUT &= ~BIT3;
    error = AppCHRONOAMPInit(AppBuff, APPBUFF_SIZE);
    if (error != AD5940ERR_OK)
    {
        CAError("INITIALIZATION");
        CAStop("INITIALIZATION_ERROR");
        return;
    }
    for (hold = 0; hold < (uint32_t)a[3] && !ec_stop; hold++)
        AD5940_Delay10us(100);
    if (ec_stop)
    {
        ec_stop = 0;
        CAStop("USER_STOP");
        return;
    }
    CAMetadata();
    writeStringUART("# CA_RUN_BEGIN");
    writeNewLine();
    ca_running = 1;
    error = AppCHRONOAMPCtrl(CHRONOAMPCTRL_PULSETEST, 0);
    if (error != AD5940ERR_OK)
    {
        CAError("START");
        CAStop("START_ERROR");
        return;
    }
    CAState();
}
int main(void)
{
    WDTCTL = WDTPW | WDTHOLD;
    initClockTo16MHz();
    initGPIO();
    initUart();
    CAMetadata();
    writeStringUART("# CA_STATE,INITIALIZING");
    writeNewLine();
    AD5940_MCUResourceInit(0);
    ca_platform_ready = (AD5940PlatformCfg() == AD5940ERR_OK);
    if (ca_platform_ready) AD5940AMPStructInit();
    else CAError("LFOSC_INITIALIZATION_TIMEOUT");
    CAMetadata();
    CAState();
    for (;;)
    {
        char command;
        uint8_t argc;
        int32_t args[EC_MAX_ARGS];
        if (ec_stop)
        {
            ec_stop = 0;
            if (ca_running && ca_count) CATransmit("USER_STOP");
            else CAStop("USER_STOP");
        }
        if (ec_bad_command)
        {
            ec_bad_command = 0;
            CAError("INVALID_OR_BUSY_COMMAND");
        }
        if (ec_take(&command, &argc, args))
        {
            if (command == 'M')
            {
                CAMetadata();
                CAState();
            }
            else if (command == 'S')
                CAStart(args, argc);
        }
        if (ca_running && AD5940_GetMCUIntFlag())
        {
            uint32_t count = APPBUFF_SIZE, i;
            AppCHRONOAMPCfg_Type *cfg;
            AD5940Err error;
            AD5940_ClrMCUIntFlag();
            error = AppCHRONOAMPISR(AppBuff, &count);
            AppCHRONOAMPGetCfg(&cfg);
            if (error != AD5940ERR_OK)
            {
                CAError("FIFO_OR_BUFFER_ERROR");
                CAStop("ACQUISITION_ERROR");
                continue;
            }
            if (ca_count + count > CA_MAX_SAMPLES)
            {
                CAError("CAPTURE_BUFFER_FULL");
                CAStop("CAPTURE_BUFFER_FULL");
                continue;
            }
            for (i = 0; i < count; i++)
                ca_samples[ca_count++] = ((float *)AppBuff)[i];
            if (cfg->EndSeq && !ec_stop)
                CATransmit("COMPLETED");
        }
    }
}
