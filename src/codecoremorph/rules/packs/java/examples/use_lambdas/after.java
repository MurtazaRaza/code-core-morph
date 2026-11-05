Collections.sort(words, (a, b) -> a.length() - b.length());
executor.submit(this::flush);
