using System;

namespace Zoo
{
    /// <summary>An animal of the zoo: a hierarchy with abstract and virtual members.</summary>
    public abstract class Animal
    {
        protected Animal(string name)
        {
            if (name == null)
            {
                throw new ArgumentNullException("name");
            }
            Name = name;
        }

        public string Name { get; private set; }

        public abstract string Sound();

        public virtual int Legs
        {
            get { return 4; }
        }

        public override string ToString()
        {
            return Name + " says " + Sound();
        }
    }

    public class Dog : Animal
    {
        public Dog(string name)
            : base(name)
        {
        }

        public override string Sound()
        {
            return "woof";
        }
    }

    public sealed class Bird : Animal
    {
        public Bird(string name)
            : base(name)
        {
        }

        public override string Sound()
        {
            return "tweet";
        }

        public override int Legs
        {
            get { return 2; }
        }
    }
}
