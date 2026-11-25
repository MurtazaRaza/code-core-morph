using System;
using System.IO;
using System.Linq.Expressions;
using System.Runtime.Serialization;
using System.Threading.Tasks;

namespace Zoo
{
    // One type for each thing the first version of a port refuses (D-78).

    // Async code the tasks runtime doesn't take over (D-96): .NET's own I/O, and async void.
    public class Loader
    {
        public async Task<string> LoadAsync(string path)
        {
            using (var reader = new StreamReader(path))
            {
                return await reader.ReadToEndAsync();
            }
        }

        public async void Fire()
        {
            await Task.Yield();
        }
    }

    public class Dynamo
    {
        public object Twice(dynamic value)
        {
            return value + value;
        }
    }

    public class Mirror
    {
        public object Make(Type type)
        {
            return Activator.CreateInstance(type);
        }

        public int Properties(Type type)
        {
            return type.GetProperties().Length;
        }
    }

    public class Pointer
    {
        public unsafe int First(int[] values)
        {
            fixed (int* first = values)
            {
                return *first;
            }
        }
    }

    public class Query
    {
        public Expression<Func<int, bool>> Big()
        {
            return x => x > 10;
        }
    }

    [DataContract]
    public class Record
    {
        [DataMember]
        public int Id { get; set; }

        public byte[] Save()
        {
            var serializer = new DataContractSerializer(typeof(Record));
            using (var stream = new MemoryStream())
            {
                serializer.WriteObject(stream, this);
                return stream.ToArray();
            }
        }
    }
}
