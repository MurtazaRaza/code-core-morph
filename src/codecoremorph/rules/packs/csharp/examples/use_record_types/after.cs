// Callers read Sku and Quantity: the positional record keeps both names.
public sealed record Line(string Sku, int Quantity);
