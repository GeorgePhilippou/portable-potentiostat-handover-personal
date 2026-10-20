import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function_source(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class NativeADCConversion(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
    def test_gpio_interrupt_defers_spi_and_preserves_unrelated_flags(self):
        port = (ROOT / 'MSP430_FR5969_Port.c').read_text()
        handler = function_source(port, '__interrupt void Port_4(void)').replace('__interrupt ', '')
        source = '''#include <stdint.h>
#include <assert.h>
#define AD5941_INT_PIN 2u
#define LED_PIN 1u
#define AFEINTSRC_SINC2RDY 4u
static unsigned P4IFG, P1OUT;
static unsigned spi_calls;
static volatile uint8_t ucInterrupted;
void AD5940_INTCClrFlag(unsigned flag) { spi_calls++; }
'''
        source += handler
        source += '''
int main(void) {
    P4IFG=6; /* AD5941 flag plus an unrelated GPIO flag. */
    Port_4();
    assert(ucInterrupted==1);
    assert(P4IFG==4);
    assert(P1OUT==1);
    assert(spi_calls==0); /* Foreground SPI transaction cannot be interrupted. */
    ucInterrupted=0;
    Port_4();
    assert(ucInterrupted==0);
    assert(P1OUT==1);
    assert(spi_calls==0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'irq_test.c';path.write_text(source)
            exe=Path(folder)/'irq_test'
            subprocess.run(['cc', '-std=c99', str(path), '-o', str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)

    @unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
    def test_actual_conversion_bounds_and_invalid_register_rejection(self):
        library = (ROOT / 'ad5940_lib/ad5940.c').read_text()
        firmware = (ROOT / 'main.c').read_text()
        source = '''#include <stdint.h>
#include <assert.h>
#include <math.h>
#include "ad5940_lib/ad5940.h"
static ADCBaseCfg_Type adc_base;
static float adc_reference_v = 1.82f;
static float voltage_output_sign = -1.0f;
'''
        source += function_source(library, 'float AD5940_ADCCode2Volt(')
        source += '\n' + function_source(firmware, 'static BoolFlag OCPConvertResult(')
        source += '''
int main(void) {
    const float gains[] = {1, 1.5f, 2, 4, 9};
    float voltage;
    for (unsigned gain=0; gain<5; gain++) {
        adc_base.ADCPga= gain;
        for (uint32_t code=0; code<=65535; code++) {
            assert(OCPConvertResult(code, &voltage));
            assert(isfinite(voltage));
            assert(fabsf(voltage)<=1.8351f/gains[gain]);
        }
    }
    voltage=42;
    assert(!OCPConvertResult(0x20202020UL, &voltage));
    assert(!OCPConvertResult(0xFFFFFFFFUL, &voltage));
    assert(voltage==42); /* Invalid read cannot become a plotted voltage. */
    adc_base.ADCPga=ADCPGA_1;
    assert(OCPConvertResult(32768, &voltage));
    assert(voltage==0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'adc_test.c';path.write_text(source)
            exe=Path(folder)/'adc_test'
            subprocess.run(['cc', '-std=c99', '-I'+str(ROOT), str(path), '-lm', '-o', str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)
