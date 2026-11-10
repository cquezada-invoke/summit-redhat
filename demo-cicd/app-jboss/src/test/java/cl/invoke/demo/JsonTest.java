package cl.invoke.demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.Test;

class JsonTest {
    @Test
    void escribeEstructurasYEscapa() {
        String s = Json.escribir(Json.m("a", "x\"<y>\n", "n", 3, "m", new BigDecimal("1.50"), "l", List.of(true), "z", null));
        assertEquals("{\"a\":\"x\\\"\\u003cy>\\n\",\"n\":3,\"m\":1.50,\"l\":[true],\"z\":null}", s);
    }

    @Test
    void elEsquemaSeDivideEnSentencias() {
        List<String> l = Bd.sentencias("-- comentario; con punto y coma\nCREATE TABLE a (x int);\n\nCREATE INDEX b ON a (x);\n");
        assertEquals(2, l.size());
        assertEquals("CREATE TABLE a (x int)", l.get(0));
    }
}
