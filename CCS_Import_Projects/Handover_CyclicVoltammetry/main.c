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
#include "voltammetry/RampTest.h"

#define APPBUFF_SIZE 1024

#define LED_PIN BIT0

#pragma DATA_SECTION(AppBuff, ".TI.persistent")

uint32_t AppBuff[APPBUFF_SIZE];
float LFOSCFreq; /* Measured LFOSC frequency */

////////////////////////////////// MSP430 Definitions
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
//////////////////////////////////////////////////////////////////////

// my delay counters
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
// function: setup UART (UCA0) to send chars at 9600 baud, 8 data bits, 1 stop bit
// SMCLK = 16 MHz: N=1666.67, UCBRx=104, UCBRFx=2, UCBRSx=0xD6 (TI SLAU367 Table 22-5)
void initUart()
{
    UCA0CTLW0 = UCSWRST;                       // Put eUSCI in reset
    UCA0CTLW0 |= UCSSEL__SMCLK;                // Select SMCLK
    UCA0BRW = 104;                             // Baud rate divisor for 9600 baud
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
// input: none
// return: none
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

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// MCU Clock
/////////////////////// function: set clock to 16 MHz
void initClockTo16MHz()
{
    // Configure one FRAM wait state as required by the data sheet for MCLK
    // operation beyond 8 MHz _before_ configuring the clock system.
    FRCTL0 = FRCTLPW | NWAITS_1;

    // configure the XTAL input pins
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
    //
    CSCTL0_H = 0; // Lock CS registers
} // end function

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// AD5941 Chrono Initialisation
/**
 * @brief The general configuration to AD5940 like FIFO/Sequencer/Clock.
 * @note This function will firstly reset AD5940 using reset pin.
 * @return return 0.
 */
static int32_t AD5940PlatformCfg(void)
{
    CLKCfg_Type clk_cfg;
    SEQCfg_Type seq_cfg;
    FIFOCfg_Type fifo_cfg;
    AGPIOCfg_Type gpio_cfg;
    LFOSCMeasure_Type LfoscMeasure;

    /* Use hardware reset */
    AD5940_HWReset();
    AD5940_Initialize(); /* Call this right after AFE reset */
    /* Platform configuration */
    /* Step1. Configure clock */
    clk_cfg.HFOSCEn = bTRUE;
    clk_cfg.HFXTALEn = bFALSE;
    clk_cfg.LFOSCEn = bTRUE;
    clk_cfg.HfOSC32MHzMode = bFALSE;
    clk_cfg.SysClkSrc = SYSCLKSRC_HFOSC;
    clk_cfg.SysClkDiv = SYSCLKDIV_1;
    clk_cfg.ADCCLkSrc = ADCCLKSRC_HFOSC;
    clk_cfg.ADCClkDiv = ADCCLKDIV_1;
    AD5940_CLKCfg(&clk_cfg);
    /* Step2. Configure FIFO and Sequencer*/
    /* Configure FIFO and Sequencer */
    fifo_cfg.FIFOEn = bTRUE; /* We will enable FIFO after all parameters configured */
    fifo_cfg.FIFOMode = FIFOMODE_FIFO;
    fifo_cfg.FIFOSize = FIFOSIZE_2KB; /* 2kB for FIFO, The reset 4kB for sequencer */
    fifo_cfg.FIFOSrc = FIFOSRC_SINC3; /* */
    fifo_cfg.FIFOThresh = 4;          /*  Don't care, set it by application paramter */
    AD5940_FIFOCfg(&fifo_cfg);
    seq_cfg.SeqMemSize = SEQMEMSIZE_4KB; /* 4kB SRAM is used for sequencer, others for data FIFO */
    seq_cfg.SeqBreakEn = bFALSE;
    seq_cfg.SeqIgnoreEn = bTRUE;
    seq_cfg.SeqCntCRCClr = bTRUE;
    seq_cfg.SeqEnable = bFALSE;
    seq_cfg.SeqWrTimer = 0;
    AD5940_SEQCfg(&seq_cfg);
    /* Step3. Interrupt controller */
    AD5940_INTCCfg(AFEINTC_1, AFEINTSRC_ALLINT,
                   bTRUE); /* Enable all interrupt in INTC1, so we can check INTC flags */
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    AD5940_INTCCfg(AFEINTC_0, AFEINTSRC_DATAFIFOTHRESH | AFEINTSRC_ENDSEQ | AFEINTSRC_CUSTOMINT0,
                   bTRUE);
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    /* Step4: Configure GPIO */
    gpio_cfg.FuncSet =
        GP0_INT | GP1_SLEEP |
        GP2_SYNC; /* GPIO1 indicates AFE is in sleep state. GPIO2 indicates ADC is sampling. */
    gpio_cfg.InputEnSet = 0;
    gpio_cfg.OutputEnSet = AGPIO_Pin0 | AGPIO_Pin1 | AGPIO_Pin2;
    gpio_cfg.OutVal = 0;
    gpio_cfg.PullEnSet = 0;
    AD5940_AGPIOCfg(&gpio_cfg);
    /* Measure LFOSC frequency */
    /**@note Calibrate LFOSC using system clock. The system clock accuracy decides measurement
     * accuracy. Use XTAL to get better result. */
    LfoscMeasure.CalDuration = 1000.0;        /* 1000ms used for calibration. */
    LfoscMeasure.CalSeqAddr = 0;              /* Put sequence commands from start address of SRAM */
    LfoscMeasure.SystemClkFreq = 16000000.0f; /* 16MHz in this firmware. */
    if (AD5940_LFOSCMeasure(&LfoscMeasure, &LFOSCFreq) != AD5940ERR_OK)
        return AD5940ERR_TIMEOUT;
    // printf("LFOSC Freq:%f\n", LFOSCFreq);
    AD5940_SleepKeyCtrlS(SLPKEY_UNLOCK);
    return 0;
}

/////////////////////////////////////////////////////////////////////////////////////////////
/// Parameters for Application
/**
 * @brief The interface for user to change application paramters.
 * @return return 0.
 */

/* Scan rate in mV/s — shared by both CV and LSV modes */
#define SCAN_RATE 50.00f

/* Sweep mode — change this to switch technique:
 *   0 = CV  (full triangular sweep: start → peak → start)
 *   1 = LSV (linear sweep voltammetry: start → peak only)  */
#define SWEEP_MODE 1

/* ---- Stage 1 potential-path diagnostic ---------------------------------------
 * Set DC_TEST_MODE to 1 to bypass the ramp and drive fixed raw AD5941 bias
 * values (VBIAS - VZERO). For each setpoint the firmware independently measures
 * VBIAS, SE0 (working electrode), and RE0 relative to VZERO using the AD5941 ADC.
 * It then reports the conventional electrochemical potential:
 *
 *     E_WE-RE = (VSE0 - VZERO) - (VRE0 - VZERO)
 *
 * This deliberately distinguishes the ADI sensor-bias convention from the
 * laboratory-potentiostat convention. Verify selected values with a high-input-
 * impedance DMM connected red to WE/SE0 and black to RE0.
 *
 * Auto-step mode: the firmware walks DC_TEST_STEPS_MV in order ONCE, holding each
 * for DC_TEST_DWELL_S seconds and announcing it over UART. After the last step it
 * holds the final setpoint, prints POT_DIAG_DONE and idles (slow LED blink) —
 * press the MSP430 reset button to run the sequence again. Watch the DMM and record
 * commanded vs measured, no rebuild per setpoint.
 * Set DC_TEST_MODE back to 0 for normal LSV/CV operation.                       */
#define DC_TEST_MODE 0        /* 1 = Stage 1 static diagnostic, no ramp     */
#define DC_TEST_DWELL_S 30    /* seconds to hold each setpoint              */
#define DC_TEST_SETTLE_MS 500 /* settle before internal voltage readings    */
#define DC_TEST_STEPS_MV {-800.0f, -300.0f, 0.0f, 300.0f, 800.0f}

/* ==================== EXPERIMENT DEFAULT CONFIGURATION ==================
 * Edit ADC inputs/gain/reference/RTIA here, like the original examples.
 * The following parameters set startup defaults; Start applies the dashboard
 * run settings. Keep bias conversion and acquisition safety checks intact.
 */
void AD5940RampStructInit(void)
{
    AppRAMPCfg_Type *pRampCfg;

    AppRAMPGetCfg(&pRampCfg);

    ///////////////////////////////////////////////////////////////////////// ADC INPUT CONFIG
    // Keep ONE positive and ONE negative input assignment active.
    // Current conversion requires the established TIA path below.
    // Internal-voltage tests belong in OCP: changing this path is no longer
    // an electrode-current measurement even if the graph still says microamps.

    ////////////// Positive Input
    // pRampCfg->ADCMuxP = ADCMUXP_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pRampCfg->ADCMuxP = ADCMUXP_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pRampCfg->ADCMuxP = ADCMUXP_LPTIA0_P; // Established current signal

    ////////////// Negative Input
    // pRampCfg->ADCMuxN = ADCMUXN_VSET1P1;   // Internal 1.11 V (voltage diagnostic only)
    // pRampCfg->ADCMuxN = ADCMUXN_AIN0;      // Version 2 J3 pin 4 (voltage only)
    pRampCfg->ADCMuxN = ADCMUXN_LPTIA0_N; // Established current reference

    ////////////// ADC Set Gain
    // pRampCfg->AdcPgaGain = ADCPGA_1;
    pRampCfg->AdcPgaGain = ADCPGA_1P5; // 1.5x

    ////////////// ADC Reference
    pRampCfg->ADCRefVolt = 1820.0f; // Millivolts; measure VREF_1V8 if calibrating

    ////////////// TIA Gain Resistor
    // pRampCfg->LPTIARtiaSel = LPTIARTIA_20K;
    pRampCfg->LPTIARtiaSel = LPTIARTIA_10K; // Nominal 10 kOhm; calibrated at startup

    ///////////////////////////////////////////////////////////////////////// ADC FILTER CONFIG
    // pRampCfg->ADCSinc3Osr = ADCSINC3OSR_4;
    pRampCfg->ADCSinc3Osr = ADCSINC3OSR_2; // Preserve the ramp's acquisition filter
    // Ramp uses SINC3 FIFO data; its SINC2 setting does not set the sample rate.

    ///////////////////////////////////////////////////////////////////////// EXPERIMENT PARAMETERS

    /* Step1 : configure general parmaters */
    pRampCfg->SeqStartAddr = 0x10;     /* leave 16 commands for LFOSC calibration.  */
    pRampCfg->MaxSeqLen = 1024 - 0x10; /* 4kB/4 = 1024  */
    pRampCfg->RcalVal = 200.0;         /* 200Ohm RCAL */
    pRampCfg->FifoThresh =
        1; /* Maximum value is 2kB/4-1 = 512-1. Set it to higher value to save power. */
    pRampCfg->SysClkFreq = 16000000.0f; /* System clock is 16MHz by default */
    pRampCfg->LFOSCClkFreq = LFOSCFreq; /* LFOSC frequency */

    /* Step2 : Configure ramp signal parameters */
    /* User-facing potentials use the normal laboratory convention E_WE - E_RE.
     * Edit these two values just as before; keep the conversion below intact. */
    const float startPotentialMv = -200.0f;
    const float peakPotentialMv = +800.0f;

    /* RampTest internally requires the opposite AD5941 VBIAS - VZERO polarity. */
    pRampCfg->RampStartVolt = -startPotentialMv;
    pRampCfg->RampPeakVolt = -peakPotentialMv;

    pRampCfg->VzeroStart = 1300.0f; /* 1.3V */
    pRampCfg->VzeroPeak = 1300.0f;  /* 1.3V */

    pRampCfg->StepNumber = 100;    /* Total steps. Equals to ADC sample number */
    pRampCfg->SampleDelay = 10.0f; /* Time between DAC update and ADC sample, ms */

    pRampCfg->bRampOneDir = (SWEEP_MODE == 1) ? bTRUE : bFALSE;

    /* For CV the ramp travels the voltage range twice (up then down),
     * so double voltageRange before computing the total sweep duration. */
    float voltageRange = fabs(pRampCfg->RampPeakVolt - pRampCfg->RampStartVolt);
    if (pRampCfg->bRampOneDir == bFALSE)
        voltageRange *= 2.0f;
    pRampCfg->RampDuration = (voltageRange / SCAN_RATE) * 1000.0f;

    //  pRampCfg->RampDuration = 10*1000;            /* X * 1000, where x is total duration of ramp
    //  signal. Unit is ms. */

    /* Maximum current decides RTIA value */  /* Maximum current decides RTIA value: Imax = 0.9V
                                                 / RTIA */
    pRampCfg->LPTIARloadSel = LPTIARLOAD_10R; // was SHORT
}

//////////////////////////////////////////////////////  Display through UART

#if DC_TEST_MODE
/* Measure one AD5941 ADC differential input in mV. The application configures
 * and calibrates the ADC before this helper is used. FIFO is disabled in Stage 1
 * so these diagnostic conversions cannot contaminate normal ramp data. */
static float MeasureAFEDifferentialMv(uint32_t mux_p, uint32_t mux_n, uint32_t adc_pga,
                                      float adc_ref_mv)
{
    uint32_t i;
    uint32_t adc_sum = 0;

    AD5940_WakeUp(10);
    AD5940_ADCMuxCfgS(mux_p, mux_n);
    AD5940_AFECtrlS(AFECTRL_ADCPWR, bTRUE);
    AD5940_Delay10us(25); /* 250 us analog power-up */
    AD5940_AFECtrlS(AFECTRL_ADCCNV, bTRUE);
    AD5940_Delay10us(100); /* 1 ms mux/filter settling */

    for (i = 0; i < 16; i++)
    {
        AD5940_Delay10us(1); /* use successive SINC3 results */
        adc_sum += AD5940_ReadAfeResult(AFERESULT_SINC3) & 0xffffu;
    }

    AD5940_AFECtrlS(AFECTRL_ADCPWR | AFECTRL_ADCCNV, bFALSE);
    return AD5940_ADCCode2Volt(adc_sum / 16u, adc_pga, adc_ref_mv);
}

static void ReportPotentialDiagnostic(float raw_bias_cmd_mv, uint32_t vbias12, uint32_t vzero6,
                                      const AppRAMPCfg_Type *pRampCfg)
{
    float vbias_rel_mv;
    float vse_rel_mv;
    float vre_rel_mv;
    float ewe_re_mv;
    float desired_ewe_re_mv = -raw_bias_cmd_mv;

    vbias_rel_mv = MeasureAFEDifferentialMv(ADCMUXP_VBIAS0, ADCMUXN_VZERO0, pRampCfg->AdcPgaGain,
                                            pRampCfg->ADCRefVolt);
    vse_rel_mv = MeasureAFEDifferentialMv(ADCMUXP_VSE0, ADCMUXN_VZERO0, pRampCfg->AdcPgaGain,
                                          pRampCfg->ADCRefVolt);
    vre_rel_mv = MeasureAFEDifferentialMv(ADCMUXP_VRE0, ADCMUXN_VZERO0, pRampCfg->AdcPgaGain,
                                          pRampCfg->ADCRefVolt);
    ewe_re_mv = vse_rel_mv - vre_rel_mv;

    /* Restore the normal current-measurement ADC mux before returning. */
    AD5940_ADCMuxCfgS(ADCMUXP_LPTIA0_P, ADCMUXN_LPTIA0_N);

    writeStringUART("# POT_DIAG_BEGIN,");
    writeFloatUART(raw_bias_cmd_mv);
    writeStringUART("# POT_DIAG_RAW_BIAS_CMD_MV,");
    writeFloatUART(raw_bias_cmd_mv);
    writeStringUART("# POT_DIAG_DESIRED_EWE_RE_MV,");
    writeFloatUART(desired_ewe_re_mv);
    writeStringUART("# POT_DIAG_VBIAS12_CODE,");
    writeFloatUART((float)vbias12);
    writeStringUART("# POT_DIAG_VZERO6_CODE,");
    writeFloatUART((float)vzero6);
    writeStringUART("# POT_DIAG_VBIAS_MINUS_VZERO_MV,");
    writeFloatUART(vbias_rel_mv);
    writeStringUART("# POT_DIAG_VSE_MINUS_VZERO_MV,");
    writeFloatUART(vse_rel_mv);
    writeStringUART("# POT_DIAG_VRE_MINUS_VZERO_MV,");
    writeFloatUART(vre_rel_mv);
    writeStringUART("# POT_DIAG_MEASURED_EWE_RE_MV,");
    writeFloatUART(ewe_re_mv);
    writeStringUART("# POT_DIAG_BIAS_ERROR_MV,");
    writeFloatUART(vbias_rel_mv - raw_bias_cmd_mv);
    writeStringUART("# POT_DIAG_CONTROL_ERROR_MV,");
    writeFloatUART(ewe_re_mv - desired_ewe_re_mv);
    writeStringUART("# POT_DIAG_END,");
    writeFloatUART(raw_bias_cmd_mv);
}
#endif

/* Emit only conventional electrochemical potentials to the dashboard. The raw
 * AD5941 values remain private to the firmware/DAC layer. */
static void WriteSweepMetadata(const AppRAMPCfg_Type *pRampCfg)
{
    writeStringUART("# CV_META_BUILD_ID,HANDOVER_2026_10_13_CV_LSV_R3");
    writeNewLine();
    writeStringUART("# CV_META_SCHEMA,MSP430_CV_UART_V1");
    writeNewLine();
    writeStringUART("# CV_META_UART_BAUD,9600");
    writeNewLine();
    writeStringUART("# CV_META_TIME_SOURCE,AD5941_WUPT_SCHEDULE");
    writeNewLine();
    writeStringUART("# CV_META_START_MV,");
    writeFloatUART(-pRampCfg->RampStartVolt);
    writeStringUART("# CV_META_PEAK_MV,");
    writeFloatUART(-pRampCfg->RampPeakVolt);
    writeStringUART("# CV_META_STEPS,");
    writeFloatUART((float)pRampCfg->StepNumber);
    writeStringUART("# CV_META_DURATION_MS,");
    writeFloatUART((float)pRampCfg->RampDuration);
    writeStringUART("# CV_META_ONE_DIR,");
    writeFloatUART((float)pRampCfg->bRampOneDir);
    writeStringUART("# CV_META_SAMPLE_DELAY_MS,");
    writeFloatUART(pRampCfg->SampleDelay);
    writeStringUART("# CV_META_VZERO_MV,");
    writeFloatUART(pRampCfg->VzeroStart);
    writeStringUART("# CV_META_RTIA_CALIBRATED_OHM,");
    writeFloatUART(pRampCfg->RtiaValue.Magnitude);
    writeStringUART("# CV_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE");
    writeNewLine();
    writeStringUART("# CV_META_CURRENT_CONVENTION,ANODIC_POSITIVE");
    writeNewLine();
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////
/// Main
static uint8_t cv_platform_ready = 0;
static uint8_t cv_running = 0;
static uint32_t cv_index = 0, cv_sweep = 0, cv_cycles = 1;
static void CVShowState(const char *state)
{
    writeStringUART("# CV_STATE,");
    writeStringUART(state);
    writeNewLine();
}
static void CVError(const char *reason)
{
    writeStringUART("# CV_ERROR,");
    writeStringUART(reason);
    writeNewLine();
}
static void CVStop(const char *reason)
{
    AD5940_SEQCtrlS(bFALSE);
    AppRAMPCtrl(APPCTRL_SHUTDOWN, 0);
    AD5940_ClrMCUIntFlag();
    P2OUT |= BIT3; /* Restore switch default; P2.3 is unused on Version 2. */
    if (cv_running)
    {
        writeStringUART("# CV_RUN_END,");
        writeStringUART(reason);
        writeNewLine();
    }
    cv_running = 0;
    CVShowState(cv_platform_ready ? "IDLE" : "FAULT");
}
/* Validate and apply a dashboard CV/LSV run, then start acquisition. */
static void CVStart(int32_t *a, uint8_t argc, AppRAMPCfg_Type *cfg)
{
    float duration, period;
    if (!cv_platform_ready)
    {
        CVError("PLATFORM_NOT_READY");
        CVShowState("FAULT");
        return;
    }
    if (argc != 8 || cv_running || a[0] < -800 || a[0] > 800 || a[1] < -800 || a[1] > 800 ||
        a[0] == a[1] || a[2] < 4 || a[2] > 2000 || a[3] < 1 || a[3] > 1000 || a[4] < 1 ||
        a[5] < 1 || a[5] > 100 || (a[6] != 0 && a[6] != 1) || a[7] < 0 || a[7] > 60000 ||
        (!a[6] && a[2] % 2))
    {
        CVError("INVALID_PARAMETERS_OR_RUNNING");
        return;
    }
    duration = fabs((float)(a[1] - a[0])) * (a[6] ? 1.0f : 2.0f) * 1000.0f / (float)a[3];
    period = duration / (float)a[2];
    /* At 9600 baud, keep framed telemetry below available serial bandwidth. */
    if (period < 100.0f || period > 10000.0f || (float)a[4] > period - 2.0f)
    {
        CVError("INVALID_STEP_PERIOD_OR_SAMPLE_DELAY");
        return;
    }
    cfg->RampStartVolt = -(float)a[0];
    cfg->RampPeakVolt = -(float)a[1];
    cfg->StepNumber = (uint32_t)a[2];
    cfg->RampDuration = duration;
    cfg->SampleDelay = (float)a[4];
    cfg->bRampOneDir = a[6] ? bTRUE : bFALSE;
    cfg->bParaChanged = bTRUE;
    cfg->StopRequired = bFALSE;
    cfg->bTestFinished = bFALSE;
    cv_cycles = (uint32_t)a[5];
    cv_sweep = 0;
    cv_index = 0;
    P2OUT &= ~BIT3;
    if (AppRAMPInit(AppBuff, APPBUFF_SIZE) != AD5940ERR_OK)
    {
        CVError("INITIALIZATION");
        CVStop("INITIALIZATION_ERROR");
        return;
    }
    /* Optional initial hold, broken into 1ms pieces so Stop remains responsive. */
    {
        uint32_t hold;
        for (hold = 0; hold < (uint32_t)a[7] && !ec_stop; hold++)
            AD5940_Delay10us(100);
    }
    if (ec_stop)
    {
        ec_stop = 0;
        CVStop("USER_STOP");
        return;
    }
    WriteSweepMetadata(cfg);
    writeStringUART("# CV_RUN_BEGIN");
    writeNewLine();
    writeStringUART("# CV_SWEEP_BEGIN,0");
    writeNewLine();
    cv_running = 1;
    if (AppRAMPCtrl(APPCTRL_START, 0) != AD5940ERR_OK)
    {
        CVError("START");
        CVStop("START_ERROR");
        return;
    }
    CVShowState("RUNNING");
}
/* Transmit samples without dropping the raw startup points. */
static void CVRecords(float *data, uint32_t count, AppRAMPCfg_Type *cfg)
{
    uint32_t i;
    for (i = 0; i < count && cv_index < cfg->StepNumber; i++, cv_index++)
    {
        /* RampDacRegUpdate advances the DAC before the first ADC sample. */
        float fraction = (float)(cv_index + 1u) / (float)cfg->StepNumber;
        float start = -cfg->RampStartVolt, peak = -cfg->RampPeakVolt;
        float potential;
        if (!cfg->bRampOneDir)
            fraction = fraction <= 0.5f ? fraction * 2.0f : 2.0f - fraction * 2.0f;
        potential = start + (peak - start) * fraction;
        writeStringUART("CV,");
        writeUIntUART(cv_sweep);
        writeChar(',');
        writeUIntUART(cv_index);
        writeChar(',');
        writeUIntUART((uint32_t)((float)cv_index * cfg->RampDuration / (float)cfg->StepNumber +
                                 cfg->SampleDelay));
        writeChar(',');
        writeFloatPlainUART(potential);
        writeChar(',');
        writeFloatUART(data[i]);
    }
}

void main(void)
{
    ///////////////////////// Stop the watch-dog timer
    WDTCTL = WDTPW | WDTHOLD;

    ///////////////////////// setup the system clock to 16 MHz
    initClockTo16MHz();

    initGPIO();
    P2OUT |= BIT3;

    initUart();

    ///////////////////////// Initialise MSP430 and AD5941
    AD5940_MCUResourceInit(0);
    AD5940_Delay10us(5000);

#if !DC_TEST_MODE
    uint32_t temp;
#endif
    AppRAMPCfg_Type *pRAMPCfg;
    AD5940_Delay10us(5000);

    cv_platform_ready = (AD5940PlatformCfg() == AD5940ERR_OK);
    if (!cv_platform_ready)
        CVError("LFOSC_INITIALIZATION_TIMEOUT");
    AD5940_Delay10us(5000);

    AD5940RampStructInit();
    AD5940_Delay10us(5000);

    ///////////////////////// Send CV sweep metadata once, so the PC dashboard can
    ///////////////////////// reconstruct the potential axis without hardcoding it.
    AppRAMPGetCfg(&pRAMPCfg);
    WriteSweepMetadata(pRAMPCfg);

    // AD5940_McuSetLow();
#if DC_TEST_MODE
    AppRAMPInit(AppBuff, APPBUFF_SIZE); /* Initialize RAMP application. Provide a buffer, which is
                                           used to store sequencer commands */
    AD5940_Delay10us(100000);           /* Diagnostic settling */
#endif

#if DC_TEST_MODE
    /* ---- Stage 1 static potential-path diagnostic --------------------------
     * AppRAMPInit has configured the same LP potentiostat loop, ADC calibration,
     * VZERO, and RTIA used for LSV/CV. Walk through raw VBIAS-VZERO setpoints,
     * measure the actual AFE/electrode nodes, and hold each point for an external
     * DMM cross-check. No normal current samples are placed in the FIFO.        */
    {
        const float dc_steps_mV[] = DC_TEST_STEPS_MV;
        const uint32_t dc_n = sizeof(dc_steps_mV) / sizeof(dc_steps_mV[0]);
        const uint32_t vzero6 = (uint32_t)((pRAMPCfg->VzeroStart - 200.0f) / DAC6BITVOLT_1LSB);
        uint32_t vz6 = (vzero6 > 63) ? 63 : vzero6;
        uint32_t s, t;

        AD5940_WakeUp(10);
        AD5940_SleepKeyCtrlS(SLPKEY_LOCK);       /* keep AFE awake so DAC holds */
        AD5940_FIFOCtrlS(FIFOSRC_SINC3, bFALSE); /* isolate direct ADC readings */

        writeStringUART("# POT_DIAG_STAGE,");
        writeFloatUART(1.0f);
        writeStringUART("# POT_DIAG_DMM_ORIENTATION,RED_WE_BLACK_RE");
        writeNewLine();
        writeStringUART("# POT_DIAG_SETPOINT_COUNT,");
        writeFloatUART((float)dc_n);

        for (s = 0; s < dc_n; s++) /* one pass through the setpoints */
        {
            int32_t ramp12 = (int32_t)(dc_steps_mV[s] / DAC12BITVOLT_1LSB);
            int32_t vbias12 = (int32_t)(vz6 * 64) + ramp12;
            if (vbias12 < 0)
                vbias12 = 0;
            if (vbias12 > 4095)
                vbias12 = 4095;

            AD5940_WakeUp(10);
            AD5940_LPDAC0WriteS((uint16_t)vbias12, (uint8_t)vz6);
            AD5940_Delay10us((uint32_t)DC_TEST_SETTLE_MS * 100u);

            /* Announce the live setpoint, codes, and internally measured nodes. */
            writeStringUART("# DC_TEST_CMD_MV,");
            writeFloatUART(dc_steps_mV[s]);
            writeStringUART("# DC_TEST_VBIAS12,");
            writeFloatUART((float)vbias12);
            writeStringUART("# DC_TEST_VZERO6,");
            writeFloatUART((float)vz6);
            ReportPotentialDiagnostic(dc_steps_mV[s], (uint32_t)vbias12, vz6, pRAMPCfg);

            /* Hold DC_TEST_DWELL_S seconds; blink LED ~1 Hz as a heartbeat */
            for (t = 0; t < (uint32_t)(DC_TEST_DWELL_S) * 2; t++)
            {
                P1OUT ^= BIT0;
                AD5940_Delay10us(50000); /* 0.5 s */
            }
        }

        /* Sequence complete: HOLD the final setpoint (the DAC still carries the
         * last setpoint's codes from the loop above — we deliberately do NOT
         * rewrite it) so the last value can be measured at leisure. Idle until
         * the user presses reset. Slow ~0.25 Hz LED blink signals "done".       */
        writeStringUART("# POT_DIAG_DONE,");
        writeFloatUART((float)dc_n);
        for (;;)
        {
            P1OUT ^= BIT0;
            AD5940_Delay10us(200000); /* 2 s -> ~0.25 Hz blink */
        }
    }
#else
    CVShowState(cv_platform_ready ? "IDLE" : "FAULT");
    for (;;)
    {
        char command;
        uint8_t argc;
        int32_t args[EC_MAX_ARGS];
        if (ec_stop)
        {
            ec_stop = 0;
            CVStop("USER_STOP");
        }
        if (ec_bad_command)
        {
            ec_bad_command = 0;
            CVError("INVALID_OR_BUSY_COMMAND");
        }
        if (ec_take(&command, &argc, args))
        {
            if (command == 'M')
            {
                WriteSweepMetadata(pRAMPCfg);
                CVShowState(!cv_platform_ready ? "FAULT" : cv_running ? "RUNNING" : "IDLE");
            }
            else if (command == 'S')
                CVStart(args, argc, pRAMPCfg);
        }
        if (cv_running && AD5940_GetMCUIntFlag())
        {
            AD5940_ClrMCUIntFlag();
            temp = APPBUFF_SIZE;
            if (AppRAMPISR(AppBuff, &temp) != AD5940ERR_OK)
                CVStop("ISR_ERROR");
            else
                CVRecords((float *)AppBuff, temp, pRAMPCfg);
        }
        if (cv_running && pRAMPCfg->bTestFinished)
        {
            writeStringUART("# CV_SWEEP_END,");
            writeUIntUART(cv_sweep);
            writeChar(',');
            writeUIntUART(cv_index);
            writeNewLine();
            writeStringUART("Next Cycle.");
            writeNewLine();
            cv_sweep++;
            if (cv_sweep >= cv_cycles)
                CVStop("COMPLETED");
            else
            {
                pRAMPCfg->bTestFinished = bFALSE;
                cv_index = 0;
                if (AppRAMPInit(AppBuff, APPBUFF_SIZE) != AD5940ERR_OK)
                    CVStop("INITIALIZATION_ERROR");
                else
                {
                    writeStringUART("# CV_SWEEP_BEGIN,");
                    writeUIntUART(cv_sweep);
                    writeNewLine();
                    if (AppRAMPCtrl(APPCTRL_START, 0) != AD5940ERR_OK)
                        CVStop("START_ERROR");
                }
            }
        }
    }
#endif
}
