using System;
using System.Collections.Generic;

namespace Zoo
{
    /// <summary>A generic type the zoo uses with two type arguments.</summary>
    public class Pen<T> where T : Animal
    {
        private readonly List<T> animals = new List<T>();

        public int Count
        {
            get { return animals.Count; }
        }

        public T this[int index]
        {
            get { return animals[index]; }
        }

        public void Add(T animal)
        {
            animals.Add(animal);
        }

        public TResult Fold<TResult>(TResult seed, Func<TResult, T, TResult> step)
        {
            var result = seed;
            foreach (var animal in animals)
            {
                result = step(result, animal);
            }
            return result;
        }
    }

    public static class Pens
    {
        public static Pen<T> Of<T>(params T[] animals) where T : Animal
        {
            var pen = new Pen<T>();
            foreach (var animal in animals)
            {
                pen.Add(animal);
            }
            return pen;
        }

        public static int Legs(Pen<Bird> birds)
        {
            return birds.Fold(0, (sum, bird) => sum + bird.Legs);
        }
    }

    /// <summary>A generic type nothing instantiates.</summary>
    public class Crate<T>
    {
        public T Content { get; set; }
    }
}
