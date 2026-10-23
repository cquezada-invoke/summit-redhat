package cl.invoke.demo;

import static org.junit.jupiter.api.Assertions.assertFalse;

import org.junit.jupiter.api.Test;

class DatosTest {
    @Test
    void laVersionVieneDelBuild() {
        assertFalse(Datos.version().isBlank());
    }
}
