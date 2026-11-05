Collections.sort(words, new Comparator<String>() {
    @Override
    public int compare(String a, String b) {
        return a.length() - b.length();
    }
});
executor.submit(new Runnable() {
    public void run() {
        flush();
    }
});
