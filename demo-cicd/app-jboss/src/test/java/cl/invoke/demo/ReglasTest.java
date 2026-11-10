package cl.invoke.demo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import cl.invoke.demo.Reglas.Falla;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class ReglasTest {
    @Test
    void montosValidos() {
        assertEquals(new BigDecimal("125.50"), Reglas.monto("125.5"));
        assertEquals(new BigDecimal("10.25"), Reglas.monto("10,25"));
    }

    @Test
    void montosInvalidos() {
        assertEquals(400, assertThrows(Falla.class, () -> Reglas.monto("0")).estado());
        assertEquals(400, assertThrows(Falla.class, () -> Reglas.monto("-3")).estado());
        assertEquals(400, assertThrows(Falla.class, () -> Reglas.monto("abc")).estado());
        assertEquals(400, assertThrows(Falla.class, () -> Reglas.monto("1.234")).estado());
        assertEquals(400, assertThrows(Falla.class, () -> Reglas.monto(null)).estado());
        assertEquals(422, assertThrows(Falla.class, () -> Reglas.monto("10000.01")).estado());
    }

    @Test
    void mediosYCanales() {
        assertEquals("tarjeta", Reglas.medio("crédito"));
        assertEquals("tarjeta", Reglas.medio(null));
        assertEquals("qr", Reglas.medio("QR"));
        assertThrows(Falla.class, () -> Reglas.medio("bitcoin"));
        assertEquals("app", Reglas.canal("móvil"));
        assertEquals("web", Reglas.canal("cualquiera"));
        assertEquals("pos", Reglas.canal("pos"));
    }

    @Test
    void formatos() {
        assertTrue(Reglas.comercioValido("COM-0042"));
        assertFalse(Reglas.comercioValido("COM-42"));
        assertTrue(Reglas.cuentaValida("CTA-100101"));
        assertFalse(Reglas.cuentaValida("CTA-1' OR '1'='1"));
        assertEquals("PAG-00000123", Reglas.codigo("PAG", 123));
        assertTrue(Reglas.codigoValido("PAG-00000123"));
        assertEquals("Metropolitana", Reglas.region("Marte", "Metropolitana"));
        assertEquals("Biobío", Reglas.region("Biobío", "Metropolitana"));
        assertEquals("a b", Reglas.texto(" a\nb ", 10));
        assertEquals("abc", Reglas.texto("abcdef", 3));
    }
}
