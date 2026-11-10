using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace Zoo.MSTest2
{
    // The cases' arguments cover how MSTest 2 names them in its report.
    [TestClass]
    public class AnimalChecks
    {
        [TestMethod]
        public void ADogSaysWoof()
        {
            Assert.AreEqual("woof", new Dog("Rex").Sound());
        }

        [TestMethod("a custom name")]
        public void Custom()
        {
            Assert.AreEqual(2, new Bird("Tweety").Legs);
        }

        [DataTestMethod]
        [DataRow(1, "a")]
        [DataRow(-2, "b c")]
        [DataRow(0, null)]
        public void Ints(int number, string text)
        {
            Assert.IsTrue(number < 2);
        }

        [DataTestMethod]
        [DataRow(4.5)]
        [DataRow(7.9)]
        public void Doubles(double value)
        {
            Assert.IsTrue(value > 0);
        }

        [DataTestMethod]
        [DataRow(true)]
        public void Bools(bool value)
        {
            Assert.IsTrue(value);
        }

        [DataTestMethod]
        [DataRow(Ticket.Kind.Child)]
        public void Enums(Ticket.Kind kind)
        {
            Assert.AreEqual(6.25m, new Ticket(kind).Price());
        }

        [DataTestMethod]
        [DataRow(3, DisplayName = "three")]
        public void Named(int value)
        {
            Assert.AreEqual(3, value);
        }
    }
}
