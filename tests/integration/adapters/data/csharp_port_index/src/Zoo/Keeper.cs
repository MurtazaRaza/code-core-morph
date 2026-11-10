using System;
using System.Collections.Generic;

namespace Zoo
{
    /// <summary>Decides how much an animal eats.</summary>
    public interface IFeeder
    {
        int Feed(Animal animal);
    }

    public class ZooException : Exception
    {
        public ZooException(string message)
            : base(message)
        {
        }
    }

    /// <summary>Static state, a public field, an event and the exceptions it throws.</summary>
    public class Keeper
    {
        public const int MaxPens = 3;
        public static readonly string Title = "keeper";
        public static readonly List<string> Log = new List<string>();
        private static int hired;

        public int Rations;

        private readonly IFeeder feeder;

        public Keeper(IFeeder feeder)
        {
            this.feeder = feeder ?? throw new ArgumentNullException("feeder");
            hired++;
        }

        public static int Hired
        {
            get { return hired; }
        }

        public static Keeper Current { get; set; }

        public event Action<Animal> Fed;

        public int Round(Pen<Dog> dogs)
        {
            if (dogs.Count == 0)
            {
                throw new ZooException("an empty pen");
            }
            int eaten = 0;
            for (int i = 0; i < dogs.Count; i++)
            {
                eaten += feeder.Feed(dogs[i]);
                var handler = Fed;
                if (handler != null)
                {
                    handler(dogs[i]);
                }
            }
            Rations -= eaten;
            Log.Add("fed " + dogs.Count);
            return eaten;
        }
    }
}
