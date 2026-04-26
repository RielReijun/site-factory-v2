<section className="py-16 md:py-20 bg-background">
  <div className="container mx-auto px-4 max-w-3xl">
    <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">[[ headline ]]</h2>
    <Accordion type="single" collapsible className="space-y-2">
      [[ *items ]]<AccordionItem value="q[[ .index ]]">
        <AccordionTrigger className="font-heading font-semibold text-left">[[ .question ]]</AccordionTrigger>
        <AccordionContent className="text-muted-foreground leading-relaxed">[[ .answer ]]</AccordionContent>
      </AccordionItem>[[ / ]]
    </Accordion>
  </div>
</section>