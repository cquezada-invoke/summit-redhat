package cl.invoke.demo;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class ClavesTest {
    @Test
    void elHashVerificaSoloLaClaveCorrecta() {
        String h = Claves.hash("una-clave-de-prueba");
        assertTrue(h.startsWith("pbkdf2_sha256$"));
        assertFalse(h.contains("una-clave-de-prueba"));
        assertTrue(Claves.verificar("una-clave-de-prueba", h));
        assertFalse(Claves.verificar("otra-clave", h));
    }

    @Test
    void cadaHashLlevaSalDistinta() {
        assertNotEquals(Claves.hash("x"), Claves.hash("x"));
    }

    @Test
    void hashMalFormadoNoVerifica() {
        assertFalse(Claves.verificar("x", "texto-plano"));
        assertFalse(Claves.verificar("x", null));
        assertFalse(Claves.verificar(null, Claves.hash("x")));
    }

    @Test
    void tokensUnicos() {
        assertNotEquals(Claves.token(), Claves.token());
        assertTrue(Claves.token().length() >= 40);
    }
}
