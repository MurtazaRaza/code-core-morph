#ifndef BILLING_METER_H
#define BILLING_METER_H

namespace billing {

typedef unsigned int Reading;

// An electricity meter's readings since it was installed.
class Meter {
public:
    explicit Meter(Reading start);

    // Records a reading; one below the last reading means the counter rolled
    // over, and is not recorded.
    void record(Reading value);

    Reading consumption() const;
    unsigned int readings() const;
    bool rolledOver() const;

private:
    Reading start_;
    Reading last_;
    unsigned int count_;
    bool rolledOver_;
};

}  // namespace billing

#endif
