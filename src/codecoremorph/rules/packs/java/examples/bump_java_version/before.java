import javax.xml.bind.DatatypeConverter;

class Codec {
    String hex(byte[] data) {
        return DatatypeConverter.printHexBinary(data);
    }

    String base64(byte[] data) {
        return new sun.misc.BASE64Encoder().encode(data);
    }
}
