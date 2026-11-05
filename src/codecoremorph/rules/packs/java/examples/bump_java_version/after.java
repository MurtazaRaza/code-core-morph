import java.util.Base64;
import java.util.HexFormat;

class Codec {
    String hex(byte[] data) {
        return HexFormat.of().withUpperCase().formatHex(data); // printHexBinary wrote upper case
    }

    String base64(byte[] data) {
        return Base64.getEncoder().encodeToString(data);
    }
}
