using System.Collections.Generic;

public class Basket
{
    // BinaryFormatter is an error from .NET 8 and throws from .NET 9: copy the graph by hand.
    public Basket DeepCopy()
    {
        var copy = new Basket();
        foreach (var line in lines)
        {
            copy.lines.Add(new Line(line.Sku, line.Quantity));
        }
        return copy;
    }
}
