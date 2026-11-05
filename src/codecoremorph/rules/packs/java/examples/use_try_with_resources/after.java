try (BufferedReader reader = new BufferedReader(new FileReader(path))) {
    return reader.readLine();
}
