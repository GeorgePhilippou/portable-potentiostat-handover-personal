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

/* User configuration stays in this main.c, like the original OCP example. */
static ADCBaseCfg_Type adc_base;
static ADCFilterCfg_Type adc_filter;
static float adc_reference_v;
static float internal_reference_v;
static float voltage_output_sign;

///////////////////////////////////////////////////////////////////////////// USER CONFIGURATION
static void OCPUserConfig(void)
{
    // Define both inputs for the differential voltage measurement.
    // Keep ONE positive and ONE negative assignment active.

    ///////////////////////////////////////////////////////////////////////// ADC INPUT CONFIG
    ////////////// Positive Input
    // adc_base.ADCMuxP = ADCMUXP_VSET1P1;    // Internal approximately 1.11 V
    // adc_base.ADCMuxP = ADCMUXP_AVDDREG;    // Internal analogue regulator, about 1.8 V
    // adc_base.ADCMuxP = ADCMUXP_VREF1P8DAC;
     adc_base.ADCMuxP = ADCMUXP_VREF2P5;    // positive input set to 1.25 V internal reference
    // adc_base.ADCMuxP = ADCMUXP_AVDD_2;     // Analogue supply divided by two
    // adc_base.ADCMuxP = ADCMUXP_AIN0;       // Version 2 J3 pin 4
    // adc_base.ADCMuxP = ADCMUXP_AIN3;       // Version 2 J3 pin 7
    // adc_base.ADCMuxP = ADCMUXP_VRE0;       // Version 2 J3 pin 1, reference electrode
    // adc_base.ADCMuxP = ADCMUXP_VSE0;       // Version 2 J3 pin 3, sense electrode
//    adc_base.ADCMuxP = ADCMUXP_AGND; // Internal analogue ground

    ////////////// Negative Input
    // adc_base.ADCMuxN = ADCMUXN_AIN0;       // Version 2 J3 pin 4, WE for electrode OCP
    // adc_base.ADCMuxN = ADCMUXN_AIN3;       // Version 2 J3 pin 7
    // adc_base.ADCMuxN = ADCMUXN_VZERO0;     // DAC common-mode node; needs DAC enabled
    adc_base.ADCMuxN = ADCMUXN_VSET1P1; // Internal approximately 1.11 V

    // VREF2P5 is the internal 2.5 V buffer divided by two (about 1.25 V).
    // With VSET1P1 (about 1.11 V), expect P-N about +0.14 V; sign -1 gives -0.14 V.
    // For the AGND / VSET1P1 pair, sign -1 reports approximately +1.11 V.
    // No electrodes needed. This library has no ADCMUXN_AGND selection.
    // Zero test: select VSET1P1 on both inputs and use output sign +1.
    // Electrode OCP: select VRE0 (+), AIN0 (-), and output sign -1 for WE-RE.
    // Version 2 electrode connection: RE=J3-1, WE=J3-4; CE/SE disconnected.
    // An unconnected external ADC input is floating, not a known test voltage.

    ////////////// Output Polarity
    // voltage_output_sign = 1.0f;           // Report ADC positive minus negative
    voltage_output_sign = -1.0f; // Reverse ADC differential sign

    ////////////// ADC Set Gain
    // adc_base.ADCPga = ADCPGA_1P5;          // 1.5x
    // adc_base.ADCPga = ADCPGA_2;            // 2x; check input range before changing
    // adc_base.ADCPga = ADCPGA_4;            // 4x: smaller input range; recalibration required
    // adc_base.ADCPga = ADCPGA_9;            // 9x: only for a small, defined differential input
    adc_base.ADCPga = ADCPGA_1; // Unity gain

    ////////////// ADC References (volts; used in calibration and conversion)
    adc_reference_v = 1.82f;      // Measure VREF_1V8 if calibrating
    internal_reference_v = 1.11f; // Nominal internal reference

    ///////////////////////////////////////////////////////////////////////// ADC FILTER CONFIG
    // ADCRawData --> SINC3 --> SINC2; nominal filtered rate about 150.04 Hz.
    // adc_filter.ADCSinc3Osr = ADCSINC3OSR_2;
    adc_filter.ADCSinc3Osr = ADCSINC3OSR_4;
    adc_filter.ADCSinc2Osr = ADCSINC2OSR_1333;
    adc_filter.ADCAvgNum = ADCAVGNUM_2;  // Only used for DFT
    adc_filter.ADCRate = ADCRATE_800KHZ; // ADC clock 16 MHz
    adc_filter.BpNotch = bTRUE;          // Bypass notch, retain SINC2
    adc_filter.BpSinc3 = bFALSE;         // Use SINC3
    adc_filter.Sinc2NotchEnable = bTRUE;
    // Dashboard rate is the slower reporting rate: 0.1 to 20 readings/s.
}

/* Derive recording labels from actual input selections, avoiding a second
 * setting that could describe an internal test as an electrode measurement. */
static const char *OCPInputDescription(void)
{
    if (adc_base.ADCMuxP == ADCMUXP_AGND && adc_base.ADCMuxN == ADCMUXN_VSET1P1 &&
        voltage_output_sign == -1.0f)
        return "INTERNAL_1P1_VOLTAGE_TEST";
    if (adc_base.ADCMuxP == ADCMUXP_VRE0 && adc_base.ADCMuxN == ADCMUXN_AIN0 &&
        voltage_output_sign == -1.0f)
        return "ELECTRODES_RE0_MINUS_AIN0";
    return "CUSTOM_ADC_INPUTS";
}
static const char *OCPOutputConvention(void)
{
    if (strcmp(OCPInputDescription(), "INTERNAL_1P1_VOLTAGE_TEST") == 0)
        return "INTERNAL_REFERENCE_VOLTAGE";
    if (strcmp(OCPInputDescription(), "ELECTRODES_RE0_MINUS_AIN0") == 0)
        return "E_WE_MINUS_E_RE";
    return voltage_output_sign == 1.0f ? "ADC_POSITIVE_MINUS_NEGATIVE"
                                       : "ADC_NEGATIVE_MINUS_POSITIVE";
}

/////////////////////////////////////////////////////////////////////
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
// Delay counters
volatile unsigned int d_counter_1 = 0;
volatile unsigned int d_counter_2 = 0;

///////////////////////////////////////////////////////////////////////////// support functions
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

///////////////////////////////////////////////////////////////////////////  UART CONFIG AND UART
/// SEND FUNCTIONS
// function: setup UART (USCI_A0) to send chars at 9600 baud rate, 8 data bits, 1 stop bit
void initUart()
{

    UCA0CTLW0 = UCSWRST;                       // Put eUSCI in reset
    UCA0CTLW0 |= UCSSEL__SMCLK;                // Select SMCLK
    UCA0BRW = 104;                             // Baud rate calculation for 9600 baud
    UCA0MCTLW = UCOS16 | 0xD600 | (0x02 << 4); // Modulation parameters
    UCA0CTLW0 &= ~UCSWRST;                     // Initialize eUSCI
    UCA0IE |= UCRXIE;                          // Enable USCI_A0 RX interrupt

} // end function

//////////////////////////////////
void writeChar(char b)
{
    while (!(UCA0IFG & UCTXIFG))
        ;          // Wait for the transmit buffer to be ready
    UCA0TXBUF = b; // Send the character
}
//////////////////////////////////
// Function to write a new line to UART
void writeNewLine()
{
    writeChar('\n'); // Send newline character
    writeChar('\r'); // Send carriage return character
}

///////////////////////////////////////////////////////////////////////// GPIO CONFIG
void initGPIO()
{

    ///////////////////////// Configure UART pins (UCA0 in UART mode)
    // P2.0 - UCA0TXD
    // P2.1 - UCA0RXD
    P2SEL0 &= ~(BIT0 | BIT1); // Clear P2SEL0 for pins 0 and 1
    P2SEL1 |= BIT0 | BIT1;    // Set P2SEL1 for pins 0 and 1

    /////////////////////////

    // Configure P1.0 and P1.1 as outputs (LEDs) FOR DEBUGGING
    P1DIR |= BIT0 | BIT1;
    P1OUT &= ~(BIT0 | BIT1); // Initially turn off P1.0 and P1.1

    // ---- Analogue switch control: P2.3 ---- /// new
    P2SEL0 &= ~BIT3;
    P2SEL1 &= ~BIT3;
    P2DIR |= BIT3;
    P2OUT |= BIT3; /* Version 5: WE to AIN0 (high impedance voltage input). */

    /////////////////////// Disable the GPIO power-on default high-impedance mode to activate
    /// previously configured port settings
    PM5CTL0 &= ~LOCKLPM5;

} // end function

//////////////////////////////////////////////////////////////////////// CLOCK CONFIG
void initClockTo16MHz()
{
    FRCTL0 = FRCTLPW | NWAITS_1; // Configure one FRAM wait state

    PJSEL0 |= BIT4 | BIT5; // Configure XT1 crystal pins

    CSCTL0_H = CSKEY_H;                                   // Unlock CS registers
    CSCTL1 = DCOFSEL_4 | DCORSEL;                         // Set DCO to 16MHz
    CSCTL2 = SELA__LFXTCLK | SELS__DCOCLK | SELM__DCOCLK; // Set clock sources
    CSCTL3 = DIVA__1 | DIVS__1 | DIVM__1;                 // Set all dividers

    // Comment out as no cystal on PCB
    //    CSCTL4 &= ~LFXTOFF;                   // Enable LFXT1
    //
    //    // Wait for the oscillator to stabilize
    //    do {
    //        CSCTL5 &= ~LFXTOFFG;              // Clear XT1 fault flag
    //        SFRIFG1 &= ~OFIFG;
    //    } while (SFRIFG1 & OFIFG);

    CSCTL0_H = 0; // Lock CS registers

} // end function

////////////////////////////////////////////////////////////////////////// PGA calibration (AD5940)
static uint8_t ocp_calibrated = 0;
static void AD5940_PGA_Calibration(void)
{
    AD5940Err err; // int_32 value representing a set of responses (defined in ad5940.h)
    ADCPGACal_Type pgacal = {0};
    pgacal.SysClkFreq = 16e6;
    pgacal.AdcClkFreq = 16e6;
    pgacal.ADCSinc2Osr = ADCSINC2OSR_178;
    pgacal.ADCSinc3Osr = ADCSINC3OSR_4; // (4 is recommended)
    pgacal.TimeOut10us = 10000;         // was 10000
    pgacal.VRef1p11 = internal_reference_v;
    pgacal.VRef1p82 = adc_reference_v;
    pgacal.PGACalType = PGACALTYPE_OFFSETGAIN;
    pgacal.ADCPga = adc_base.ADCPga;
    err = AD5940_ADCPGACal(&pgacal);
    if (err != AD5940ERR_OK)
    { // if calibration failed
        writeStringUART("# OCP_ERROR,PGA_CALIBRATION");
        writeNewLine();
    }
    else
    {
        ocp_calibrated = 1;
        writeStringUART("# OCP_CALIBRATION,OK");
        writeNewLine();
    }
} // end

/* ======================= RECORDING STATE AND CLOCK ===================== */
static volatile uint32_t ocp_millis = 0;
static uint8_t ocp_running = 0;
static uint32_t ocp_adc_min = 0xFFFFFFFFUL;
static uint32_t ocp_adc_max = 0;
static uint32_t ocp_index = 0, ocp_start_ms = 0, ocp_next_ms = 0, ocp_duration_ms = 0u,
                ocp_period_ms = 100u;
static uint32_t OCPMillis(void)
{
    uint32_t result;
    __disable_interrupt();
    result = ocp_millis;
    __enable_interrupt();
    return result;
}
#pragma vector = TIMER0_A0_VECTOR
__interrupt void OCPClockISR(void)
{
    ocp_millis++;
}
static void OCPState(void)
{
    writeStringUART("# OCP_STATE,");
    writeStringUART(ocp_running ? "RUNNING" : "IDLE");
    writeNewLine();
}
/* Metadata records the input mode so internal tests cannot look like cell OCP. */
static void OCPMetadata(void)
{
    writeStringUART("# OCP_META_BUILD_ID,HANDOVER_2026_10_12_OCP_PRECISION_R6");
    writeNewLine();
    writeStringUART("# OCP_META_SCHEMA,MSP430_OCP_UART_V1");
    writeNewLine();
    writeStringUART("# OCP_META_POTENTIAL_CONVENTION,");
    writeStringUART(OCPOutputConvention());
    writeNewLine();
    writeStringUART("# OCP_META_VOLTAGE_UNITS,V");
    writeNewLine();
    writeStringUART("# OCP_META_TIME_SOURCE,MSP430_READ_TIME_MS");
    writeNewLine();
    writeStringUART("# OCP_META_INPUT_ROUTING,");
    writeStringUART(OCPInputDescription());
    writeNewLine();
    writeStringUART("# OCP_META_ADC_MUX_POSITIVE,");
    writeUIntUART(adc_base.ADCMuxP);
    writeNewLine();
    writeStringUART("# OCP_META_ADC_MUX_NEGATIVE,");
    writeUIntUART(adc_base.ADCMuxN);
    writeNewLine();
    writeStringUART("# OCP_META_ADC_PGA_ENUM,");
    writeUIntUART(adc_base.ADCPga);
    writeNewLine();
    writeStringUART("# OCP_META_ADC_REFERENCE_V,");
    writeFloatUART(adc_reference_v);
    writeStringUART("# OCP_META_OUTPUT_SIGN,");
    writeFloatUART(voltage_output_sign);
    writeStringUART("# OCP_META_ADC_RESULT_BITS,16");
    writeNewLine();
    writeStringUART("# OCP_META_UART_VOLTAGE_DECIMALS,6");
    writeNewLine();
    writeStringUART("# OCP_META_RECORD_INCLUDES_ADC_CODE,1");
    writeNewLine();
    // SPI register diagnostics are captured with session metadata.
    writeStringUART("# OCP_META_ADI_ID,0x");
    writeHex32UART(AD5940_ReadReg(REG_AFECON_ADIID));
    writeNewLine();
    writeStringUART("# OCP_META_CHIP_ID,0x");
    writeHex32UART(AD5940_ReadReg(REG_AFECON_CHIPID));
    writeNewLine();
    writeStringUART("# OCP_META_ADC_CONFIG_REGISTER,0x");
    writeHex32UART(AD5940_ReadReg(REG_AFE_ADCCON));
    writeNewLine();
    writeStringUART("# OCP_META_UART_BAUD,9600");
    writeNewLine();
    writeStringUART("# OCP_META_SAMPLE_PERIOD_MS,");
    writeUIntUART(ocp_period_ms);
    writeNewLine();
}
static void OCPStop(const char *reason)
{
    AD5940_ADCConvtCtrlS(bFALSE);
    AD5940_ADCPowerCtrlS(bFALSE);
    AD5940_ClrMCUIntFlag();
    if (ocp_running)
    {
        writeStringUART("# OCP_RUN_END,");
        writeStringUART(reason);
        writeChar(',');
        writeUIntUART(ocp_index);
        writeNewLine();
        // Keep the observed ADC code range with the saved run evidence.
        if (ocp_index)
        {
            writeStringUART("# OCP_ADC_CODE_RANGE,");
            writeUIntUART(ocp_adc_min);
            writeChar(',');
            writeUIntUART(ocp_adc_max);
            writeNewLine();
        }
    }
    ocp_running = 0;
    OCPState();
}

///////////////////////////////////////////////////////////////////////////// ADC RESULT CONVERSION
static void OCPWriteVoltageUART(float voltage)
{
    uint32_t whole, fraction, divisor;
    // Six decimal places (1 uV text precision) preserve the ADC code spacing.
    // This changes transport precision, not the ADC's physical resolution.
    if (voltage < 0)
    {
        writeChar('-');
        voltage = -voltage;
    }
    whole = (uint32_t)voltage;
    fraction = (uint32_t)((voltage - (float)whole) * 1000000.0f + 0.5f);
    if (fraction >= 1000000UL)
    {
        whole++;
        fraction = 0;
    }
    writeUIntUART(whole);
    writeChar('.');
    for (divisor = 100000UL; divisor; divisor /= 10UL)
        writeChar((char)('0' + (fraction / divisor) % 10UL));
}

static BoolFlag OCPConvertResult(uint32_t raw, float *voltage)
{
    // SINC2DAT is a 32-bit register with ADC data only in bits 15:0.
    // Upper bits are reserved, not signed voltage data. Do not turn a corrupt
    // register read into an apparently valid measurement by silently masking it.
    if ((raw & ~((uint32_t)BITM_AFE_SINC2DAT_DATA)) != 0)
        return bFALSE;
    *voltage = voltage_output_sign *
        AD5940_ADCCode2Volt(raw & (uint32_t)BITM_AFE_SINC2DAT_DATA,
                           adc_base.ADCPga, adc_reference_v);
    return bTRUE;
}
/* ======================= AD5941 HARDWARE CONFIGURATION ================== */
static void OCPConfigure(void)
{
    AFERefCfg_Type ref;
    LPLoopCfg_Type loop;
    AGPIOCfg_Type gpio;
    memset(&ref, 0, sizeof(ref));
    memset(&gpio, 0, sizeof(gpio));
    memset(&loop, 0, sizeof(loop));
    /* All potentiostat amplifiers and DACs remain off: no CE current drive. */
    AD5940_LPLoopCfgS(&loop);
    ref.HpBandgapEn = bTRUE;
    ref.Hp1V1BuffEn = bTRUE;
    ref.Hp1V8BuffEn = bTRUE;
    // VREF2P5 selects half the low-power 2.5 V reference BUFFER output.
    // Power that source when selected; the electrode-driving DAC remains off.
    ref.LpBandgapEn = (adc_base.ADCMuxP == ADCMUXP_VREF2P5) ? bTRUE : bFALSE;
    ref.LpRefBufEn = ref.LpBandgapEn;
    AD5940_REFCfgS(&ref);
    AD5940_Delay10us(1000); // Allow enabled reference buffers to settle (10 ms).
    AD5940_AFEPwrBW(AFEPWR_LP, AFEBW_250KHZ);
    // Apply the ADC selections above, after PGA calibration has completed.
    AD5940_ADCBaseCfgS(&adc_base);
    AD5940_ADCFilterCfgS(&adc_filter);
    AD5940_INTCCfg(AFEINTC_0, AFEINTSRC_SINC2RDY, bTRUE);
    // Controller 1 is polled directly; acquisition does not require a GPIO edge.
    AD5940_INTCCfg(AFEINTC_1, AFEINTSRC_SINC2RDY, bTRUE);
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    gpio.FuncSet = GP0_INT;
    gpio.OutputEnSet = AGPIO_Pin0;
    AD5940_AGPIOCfg(&gpio);
}
/* ======================= STARTUP AND EXPERIMENT LOOP ==================== */
/* Boot idle; M requests metadata, S starts a run, Stop disables conversion. */
int main(void)
{
    WDTCTL = WDTPW | WDTHOLD;
    initClockTo16MHz();
    initGPIO();
    initUart();
    AD5940_MCUResourceInit(0);
    AD5940_HWReset();
    AD5940_Initialize();
    OCPUserConfig(); // Inputs, gain, references and filters in this main.c
    AD5940_PGA_Calibration();
    OCPConfigure();
    TA0CCR0 = 1999;
    TA0CCTL0 = CCIE;
    TA0CTL = TASSEL__SMCLK | ID__8 | MC__UP | TACLR;
    OCPMetadata();
    OCPState();
    for (;;)
    {
        char command;
        uint8_t argc;
        int32_t args[EC_MAX_ARGS];
        uint32_t elapsed;
        if (ec_stop)
        {
            ec_stop = 0;
            OCPStop("USER_STOP");
        }
        if (ec_bad_command)
        {
            ec_bad_command = 0;
            writeStringUART("# OCP_ERROR,INVALID_OR_BUSY_COMMAND");
            writeNewLine();
        }
        /* Process dashboard commands separately from acquiring data. */
        if (ec_take(&command, &argc, args))
        {
            if (command == 'M')
            {
                OCPMetadata();
                OCPState();
            }
            else if (command == 'S')
            {
                if (argc != 2 || ocp_running || !ocp_calibrated || args[0] < 0 || args[1] < 50 ||
                    args[1] > 10000)
                {
                    writeStringUART("# OCP_ERROR,INVALID_PARAMETERS_OR_RUNNING");
                    writeNewLine();
                }
                else
                {
                    ocp_duration_ms = (uint32_t)args[0];
                    ocp_period_ms = (uint32_t)args[1];
                    ocp_index = 0;
                    ocp_adc_min = 0xFFFFFFFFUL;
                    ocp_adc_max = 0;
                    ocp_next_ms = 0;
                    OCPMetadata();
                    writeStringUART("# OCP_RUN_BEGIN");
                    writeNewLine();
                    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
                    AD5940_ClrMCUIntFlag();
                    ocp_start_ms = OCPMillis();
                    ocp_running = 1;
                    AD5940_AFECtrlS(AFECTRL_ADCPWR | AFECTRL_SINC2NOTCH, bTRUE);
                    AD5940_ADCConvtCtrlS(bTRUE);
                    OCPState();
                }
            }
        }
        /* Acquire and transmit only while a run is active. */
        if (!ocp_running)
            continue;
        elapsed = OCPMillis() - ocp_start_ms;
        if (ocp_duration_ms && elapsed >= ocp_duration_ms)
        {
            OCPStop("COMPLETED");
            continue;
        }
        if (elapsed >= ocp_next_ms)
        {
            uint32_t code;
            float voltage;
            AD5940_ClrMCUIntFlag();
            // Poll the ADC's actual ready flag rather than gating on GP0/P4.1.
            if (!AD5940_INTCTestFlag(AFEINTC_1, AFEINTSRC_SINC2RDY))
            {
                if (elapsed >= ocp_next_ms + 2000UL)
                {
                    writeStringUART("# OCP_ERROR,ADC_NOT_READY");
                    writeNewLine();
                    writeStringUART("# OCP_DIAG_AFECON,0x");
                    writeHex32UART(AD5940_ReadReg(REG_AFE_AFECON));
                    writeNewLine();
                    writeStringUART("# OCP_DIAG_FILTER,0x");
                    writeHex32UART(AD5940_ReadReg(REG_AFE_ADCFILTERCON));
                    writeNewLine();
                    writeStringUART("# OCP_DIAG_INTCFLAG1,0x");
                    writeHex32UART(AD5940_ReadReg(REG_INTC_INTCFLAG1));
                    writeNewLine();
                    writeStringUART("# OCP_DIAG_SINC2_RAW,0x");
                    writeHex32UART(AD5940_ReadAfeResult(AFERESULT_SINC2));
                    writeNewLine();
                    OCPStop("ADC_NOT_READY");
                }
                continue;
            }
            code = AD5940_ReadAfeResult(AFERESULT_SINC2);
            AD5940_INTCClrFlag(AFEINTSRC_SINC2RDY);
            /* Convert ADC P-minus-N, then apply the selected reporting polarity. */
            if (!OCPConvertResult(code, &voltage))
            {
                writeStringUART("# OCP_ERROR,ADC_REGISTER_INVALID,0x");
                writeHex32UART(code);
                writeNewLine();
                OCPStop("INVALID_ADC_REGISTER");
                continue;
            }
            if (code < ocp_adc_min)
                ocp_adc_min = code;
            if (code > ocp_adc_max)
                ocp_adc_max = code;
            writeStringUART("OCP,");
            writeUIntUART(ocp_index++);
            writeChar(',');
            writeUIntUART(elapsed);
            writeChar(',');
            OCPWriteVoltageUART(voltage);
            writeChar(',');
            writeUIntUART(code); // Preserve the original 16-bit code in the session CSV.
            writeNewLine();
            ocp_next_ms += ocp_period_ms;
            if (elapsed >= ocp_next_ms)
            {
                writeStringUART("# OCP_ERROR,SAMPLING_OVERRUN");
                writeNewLine();
                OCPStop("SAMPLING_OVERRUN");
            }
        }
    }
}
