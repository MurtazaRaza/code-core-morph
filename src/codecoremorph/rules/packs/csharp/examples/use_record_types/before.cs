public sealed class Line
{
    private readonly string sku;
    private readonly int quantity;

    public Line(string sku, int quantity)
    {
        this.sku = sku;
        this.quantity = quantity;
    }

    public string Sku { get { return sku; } }
    public int Quantity { get { return quantity; } }

    public override bool Equals(object obj)
    {
        return obj is Line other && sku == other.sku && quantity == other.quantity;
    }

    public override int GetHashCode()
    {
        return (sku == null ? 0 : sku.GetHashCode()) * 31 + quantity;
    }
}
