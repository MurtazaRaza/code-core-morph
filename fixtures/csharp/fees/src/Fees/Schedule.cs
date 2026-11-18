using System;
using System.Collections.Generic;
using System.Linq;

namespace Fees
{
    /// <summary>The rules charged together.</summary>
    public sealed class Schedule
    {
        private readonly List<FeeRule> rules = new List<FeeRule>();

        public int Count
        {
            get { return rules.Count; }
        }

        public void Add(FeeRule rule)
        {
            if (rule == null)
            {
                throw new ArgumentNullException(nameof(rule));
            }
            rules.Add(rule);
        }

        /// <summary>Removes the first rule with the name; false if there is none.</summary>
        public bool Remove(string name)
        {
            int index = rules.FindIndex(r => r.Name == name);
            if (index < 0)
            {
                return false;
            }
            rules.RemoveAt(index);
            return true;
        }

        /// <summary>The first rule with the name, or null.</summary>
        public FeeRule Find(string name)
        {
            return rules.FirstOrDefault(r => r.Name == name);
        }

        public decimal Total(decimal amount)
        {
            decimal total = 0m;
            foreach (FeeRule rule in rules)
            {
                total += rule.Charge(amount);
            }
            return total;
        }

        public string Report(decimal amount)
        {
            return string.Join("\n", rules.Select(r => r.Describe(amount)));
        }
    }
}
