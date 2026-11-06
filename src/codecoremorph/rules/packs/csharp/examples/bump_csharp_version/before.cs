using System.IO;
using System.Runtime.Serialization.Formatters.Binary;

public class Basket
{
    public Basket DeepCopy()
    {
        using (var stream = new MemoryStream())
        {
            var formatter = new BinaryFormatter();
            formatter.Serialize(stream, this);
            stream.Position = 0;
            return (Basket)formatter.Deserialize(stream);
        }
    }
}
