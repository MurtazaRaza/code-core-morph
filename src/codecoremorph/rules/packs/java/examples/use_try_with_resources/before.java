BufferedReader reader = new BufferedReader(new FileReader(path));
try {
    return reader.readLine();
} finally {
    reader.close();
}
