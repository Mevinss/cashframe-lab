// Local writing presets selected by a reviewer. They never inspect video data.
export const ANNOTATION_TEMPLATES = [
  {
    id: 'bag_handover_to_cashier', type_id: 'bag_handover_to_cashier', kind: 'neutral',
    labels: {ru: 'Передача сумки кассиру', kk: 'Кассирге сөмкені беру', en: 'Handing a bag to the cashier'},
    descriptions: {
      ru: 'Человек передаёт кассиру сумку. Кассир принимает её.',
      kk: 'Адам кассирге сөмкені береді. Кассир оны қабылдайды.',
      en: 'A person hands a bag to the cashier. The cashier accepts it.'
    }
  },
  {
    id: 'handle_cash_storage', type_id: 'handle_cash_storage', kind: 'neutral',
    labels: {ru: 'Работа с ящиком', kk: 'Жәшікпен жұмыс істеу', en: 'Handling a drawer'},
    descriptions: {
      ru: 'Кассир открывает ящик и выполняет действия с его содержимым.',
      kk: 'Кассир жәшікті ашып, ішіндегі заттармен әрекет жасайды.',
      en: 'The cashier opens a drawer and handles its contents.'
    }
  },
  {
    id: 'return_to_workstation', type_id: 'return_to_workstation', kind: 'neutral',
    labels: {ru: 'Возвращение к рабочему месту', kk: 'Жұмыс орнына оралу', en: 'Returning to the workstation'},
    descriptions: {
      ru: 'Сотрудник возвращается в рабочую зону и подходит к рабочему месту.',
      kk: 'Қызметкер жұмыс аймағына оралып, жұмыс орнына жақындайды.',
      en: 'The employee returns to the work area and approaches the workstation.'
    }
  },
  {
    id: 'leave_before_service_end', type_id: 'leave_before_service_end', kind: 'candidate',
    labels: {ru: 'Уход до окончания обслуживания', kk: 'Қызмет көрсету аяқталмай жұмыс орнынан кету', en: 'Leaving before service completion'},
    descriptions: {
      ru: 'Кассир покидает рабочее место, пока обслуживание клиента при приёме наличных ещё продолжается. Видны границы рабочего места и момент выхода за них.',
      kk: 'Клиенттен қолма-қол ақшаны қабылдау кезіндегі қызмет көрсету әлі жалғасып жатқанда кассир жұмыс орнынан кетеді. Жұмыс орнының шекарасы және одан шыққан сәті көрінеді.',
      en: 'The cashier leaves the workstation while a customer cash receipt operation is still in progress. The workstation boundaries and the cashier crossing them are visible.'
    }
  },
  {
    id: 'cash_receipt_count_without_client_presence', type_id: 'cash_receipt_count_without_client_presence', kind: 'candidate',
    labels: {ru: 'Приём или пересчёт без присутствия клиента', kk: 'Клиентсіз қолма-қол ақшаны қабылдау немесе санау', en: 'Receiving or counting cash without the client'},
    descriptions: {
      ru: 'Кассир принимает или пересчитывает наличные в рамках клиентской операции. Зона присутствия клиента видна полностью; клиент при выполнении операции отсутствует.',
      kk: 'Кассир клиенттік операция аясында қолма-қол ақшаны қабылдайды немесе санайды. Клиент болуы тиіс аймақ толық көрінеді; операция кезінде клиент жоқ.',
      en: 'The cashier receives or counts cash during a customer transaction. The entire client area is visible, and the client is absent during the operation.'
    }
  },
  {
    id: 'prior_cash_outside_designated_storage', type_id: 'prior_cash_outside_designated_storage', kind: 'candidate',
    labels: {ru: 'Ранее принятые деньги вне предназначенного хранения', kk: 'Бұрын қабылданған ақша арнайы сақтау орнынан тыс', en: 'Prior receipts outside designated storage'},
    descriptions: {
      ru: 'Ранее принятые деньги остаются вне предназначенного устройства хранения. По последовательности операций видно, что они относятся к предыдущей операции и отличаются от денег текущей операции.',
      kk: 'Бұрын қабылданған ақша арнайы сақтау құрылғысынан тыс қалдырылған. Операциялардың ретінен оның алдыңғы операцияға тиесілі екені және ағымдағы операцияның ақшасынан бөлек екені көрінеді.',
      en: 'Previously received cash remains outside the designated storage device. The sequence of operations shows that it belongs to an earlier transaction and is distinct from the current transaction’s cash.'
    }
  },
  {
    id: 'receipt_confirmation_not_provided', type_id: 'receipt_confirmation_not_provided', kind: 'candidate',
    labels: {ru: 'Не предоставлено подтверждение приёма наличных', kk: 'Қолма-қол ақшаны қабылдау құжатын бермеу', en: 'Receipt confirmation not provided'},
    descriptions: {
      ru: 'Приём наличных от клиента завершён, но подтверждающий документ клиенту не предоставлен. Назначение документа и доступные способы его предоставления установлены; ненаблюдаемое предоставление исключено.',
      kk: 'Клиенттен қолма-қол ақшаны қабылдау аяқталған, бірақ растайтын құжат клиентке берілмеген. Құжаттың мақсаты мен оны ұсынудың қолжетімді тәсілдері анықталған; көрінбейтін жолмен берілуі жоққа шығарылған.',
      en: 'The customer cash receipt operation has ended, but the confirmation document was not provided to the client. The document’s purpose and available delivery methods are established, and unseen delivery has been excluded.'
    }
  }
];

export function annotationTemplate(value) {
  return ANNOTATION_TEMPLATES.find(item => item.id === value || item.type_id === value || Object.values(item.labels).includes(value));
}
