record Point(int x, int y) {
    // The callers' accessors stay; equals and hashCode come from the record.
    int getX() { return x; }
    int getY() { return y; }
}
